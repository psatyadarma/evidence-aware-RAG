"""Offline benchmark loading and corpus-grounding validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union

from pydantic import ValidationError

from app.evidence import observation_evidence_id
from app.models import (
    BenchmarkComputation,
    BenchmarkExample,
    ComputationType,
    PopulationObservation,
    ProcessedDemographicRecord,
)
from app.sources import SOURCES


class BenchmarkValidationError(ValueError):
    """Benchmark ground truth is malformed or inconsistent with the corpus."""


Number = Union[int, float]
KNOWN_SOURCE_IDS = frozenset(source.source_id for source in SOURCES)


@dataclass(frozen=True)
class IndexedObservation:
    evidence_id: str
    record: ProcessedDemographicRecord
    observation: PopulationObservation


class EvidenceIndex:
    def __init__(self, observations: Iterable[IndexedObservation]) -> None:
        self._observations: dict[str, IndexedObservation] = {}
        for observation in observations:
            if observation.evidence_id in self._observations:
                raise BenchmarkValidationError(
                    f"duplicate evidence ID {observation.evidence_id!r}"
                )
            self._observations[observation.evidence_id] = observation

    def __contains__(self, evidence_id: str) -> bool:
        return evidence_id in self._observations

    def __getitem__(self, evidence_id: str) -> IndexedObservation:
        try:
            return self._observations[evidence_id]
        except KeyError as exc:
            raise BenchmarkValidationError(
                f"unknown evidence ID {evidence_id!r}"
            ) from exc

    def find(
        self,
        *,
        source_id: str,
        year: int,
        record_dimensions: dict[str, str],
        observation_dimensions: Optional[dict[str, str]] = None,
    ) -> list[IndexedObservation]:
        observation_dimensions = observation_dimensions or {}
        return [
            indexed
            for indexed in self._observations.values()
            if indexed.record.source_id == source_id
            and indexed.observation.year == year
            and all(
                indexed.record.dimensions.get(key) == value
                for key, value in record_dimensions.items()
            )
            and all(
                indexed.observation.dimensions.get(key) == value
                for key, value in observation_dimensions.items()
            )
        ]

    def numeric_value(self, evidence_id: str) -> Number:
        observation = self[evidence_id].observation
        if observation.value is None:
            raise BenchmarkValidationError(
                f"evidence {evidence_id!r} is NOT_AVAILABLE, not numeric"
            )
        return observation.value


def load_evidence_index(processed_dir: Path) -> EvidenceIndex:
    indexed: list[IndexedObservation] = []
    for source in SOURCES:
        path = processed_dir / source.processed_filename
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise BenchmarkValidationError(f"cannot read corpus file {path}: {exc}") from exc
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                raise BenchmarkValidationError(f"blank line in {path}:{line_number}")
            try:
                record = ProcessedDemographicRecord.model_validate_json(line)
            except ValidationError as exc:
                raise BenchmarkValidationError(
                    f"invalid corpus record at {path}:{line_number}: {exc}"
                ) from exc
            for observation in record.observations:
                indexed.append(
                    IndexedObservation(
                        evidence_id=observation_evidence_id(record, observation),
                        record=record,
                        observation=observation,
                    )
                )
    return EvidenceIndex(indexed)


def calculate_computation(
    computation: BenchmarkComputation, evidence_index: EvidenceIndex
) -> Union[Number, str]:
    values = [evidence_index.numeric_value(item) for item in computation.evidence_ids]
    if computation.type == ComputationType.DIFFERENCE:
        if len(values) != 2:
            raise BenchmarkValidationError("difference requires exactly two evidence IDs")
        return values[1] - values[0]
    if computation.type == ComputationType.SUM:
        return sum(values)
    if computation.type == ComputationType.COMPARISON:
        if len(values) != 2:
            raise BenchmarkValidationError("comparison requires exactly two evidence IDs")
        if values[0] == values[1]:
            return "equal"
        return "first" if values[0] > values[1] else "second"
    if computation.type == ComputationType.ARGMAX:
        maximum = max(values)
        winners = [
            evidence_id
            for evidence_id, value in zip(computation.evidence_ids, values)
            if value == maximum
        ]
        if len(winners) != 1:
            raise BenchmarkValidationError("argmax ground truth has a tie")
        return winners[0]
    raise BenchmarkValidationError(f"unsupported computation {computation.type!r}")


def validate_example(
    example: BenchmarkExample, evidence_index: EvidenceIndex
) -> None:
    for evidence_id in example.required_evidence:
        if evidence_id not in evidence_index:
            raise BenchmarkValidationError(
                f"{example.id}: nonexistent required evidence {evidence_id!r}"
            )

    for target in example.unavailable_targets:
        if target.source_id not in KNOWN_SOURCE_IDS:
            raise BenchmarkValidationError(
                f"{example.id}: unavailable target uses unknown source {target.source_id!r}"
            )
        matches = evidence_index.find(
            source_id=target.source_id,
            year=target.year,
            record_dimensions=target.record_dimensions,
            observation_dimensions=target.observation_dimensions,
        )
        if matches:
            raise BenchmarkValidationError(
                f"{example.id}: target marked unavailable now resolves to "
                f"{len(matches)} observation(s)"
            )

    if example.computation is not None:
        calculated = calculate_computation(example.computation, evidence_index)
        if calculated != example.computation.result:
            raise BenchmarkValidationError(
                f"{example.id}: stored computation result {example.computation.result!r} "
                f"does not match calculated result {calculated!r}"
            )
        if (
            example.expected_answer is not None
            and example.expected_answer.numeric_value is not None
            and isinstance(calculated, (int, float))
            and example.expected_answer.numeric_value != calculated
        ):
            raise BenchmarkValidationError(
                f"{example.id}: expected numeric answer does not match computation"
            )
    elif (
        example.expected_answer is not None
        and example.expected_answer.numeric_value is not None
        and len(example.required_evidence) == 1
    ):
        source_value = evidence_index.numeric_value(example.required_evidence[0])
        if example.expected_answer.numeric_value != source_value:
            raise BenchmarkValidationError(
                f"{example.id}: expected numeric answer does not match source observation"
            )


def load_benchmark(path: Path, evidence_index: EvidenceIndex) -> list[BenchmarkExample]:
    examples: list[BenchmarkExample] = []
    seen_ids: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BenchmarkValidationError(f"cannot read benchmark {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise BenchmarkValidationError(f"blank line in {path}:{line_number}")
        try:
            example = BenchmarkExample.model_validate_json(line)
        except ValidationError as exc:
            raise BenchmarkValidationError(
                f"invalid benchmark example at {path}:{line_number}: {exc}"
            ) from exc
        if example.id in seen_ids:
            raise BenchmarkValidationError(f"duplicate benchmark ID {example.id!r}")
        seen_ids.add(example.id)
        validate_example(example, evidence_index)
        examples.append(example)
    if not examples:
        raise BenchmarkValidationError("benchmark must contain at least one example")
    return examples


def write_benchmark(examples: Iterable[BenchmarkExample], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            for example in examples:
                output.write(
                    json.dumps(
                        example.model_dump(mode="json"),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                output.write("\n")
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
