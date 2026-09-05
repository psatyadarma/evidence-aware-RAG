"""Retrieval-unit construction and a deterministic cosine TF-IDF baseline."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable, Protocol, Sequence

from pydantic import ValidationError

from app.evidence import observation_evidence_id
from app.models import (
    ObservationStatus,
    ProcessedDemographicRecord,
    RetrievalResult,
    RetrievalUnit,
)
from app.sources import SOURCES


RETRIEVER_NAME = "tfidf_cosine_v1"
TOKEN_PATTERN = re.compile(r"\d+(?:\.\d+)?|[^\W\d_]+", flags=re.UNICODE)
SINGULAR_EXCEPTIONS = frozenset({"census", "series", "status", "singapore"})
DIMENSION_ORDER = (
    "series",
    "geography_level",
    "geography",
    "planning_region",
    "planning_area",
    "subzone",
    "sex",
    "age_group",
)


class RetrievalCorpusError(ValueError):
    """Processed data cannot be represented as retrieval units."""


class Retriever(Protocol):
    def retrieve(self, question: str, k: int) -> list[RetrievalResult]:
        """Return up to k ranked evidence units."""


def _light_singularize(token: str) -> str:
    if not token.isalpha() or token in SINGULAR_EXCEPTIONS:
        return token
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def normalize_text(text: str) -> tuple[str, ...]:
    """Normalize text with explicit, dependency-free rules for lexical matching."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = re.sub(r"(?<=\d),(?=\d)", "", normalized)
    normalized = re.sub(r"['’]s\b", "", normalized)
    normalized = normalized.replace("&", " and ")
    return tuple(_light_singularize(token) for token in TOKEN_PATTERN.findall(normalized))


def _dimension_label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def _searchable_text(
    record: ProcessedDemographicRecord,
    *,
    year: int,
    value_text: str,
    status: ObservationStatus,
    observation_dimensions: dict[str, str],
) -> str:
    dimensions = {**record.dimensions, **observation_dimensions}
    parts = [
        f"Dataset: {record.provenance.dataset_name}.",
        f"Publisher: {record.provenance.publisher}.",
        f"Year: {year}.",
    ]
    for key in DIMENSION_ORDER:
        if key in dimensions:
            parts.append(f"{_dimension_label(key)}: {dimensions[key]}.")
    for key in sorted(set(dimensions) - set(DIMENSION_ORDER)):
        parts.append(f"{_dimension_label(key)}: {dimensions[key]}.")
    if status == ObservationStatus.OBSERVED:
        parts.append(f"Published value: {value_text}.")
    else:
        marker = value_text or "null"
        parts.append(f"Published value: not available. Source marker: {marker}.")
    return " ".join(parts)


def retrieval_unit_from_observation(
    record: ProcessedDemographicRecord,
    observation_index: int,
) -> RetrievalUnit:
    try:
        observation = record.observations[observation_index]
    except IndexError as exc:
        raise RetrievalCorpusError(
            f"record {record.source_id}:r{record.record_id} has no observation "
            f"at index {observation_index}"
        ) from exc
    evidence_id = observation_evidence_id(record, observation)
    dimensions = {**record.dimensions, **observation.dimensions}
    value_text = (
        observation.raw_value
        if observation.raw_value is not None
        else str(observation.value)
        if observation.value is not None
        else ""
    )
    return RetrievalUnit(
        evidence_id=evidence_id,
        document_id=f"{record.source_id}:r{record.record_id}",
        source_id=record.source_id,
        record_id=record.record_id,
        year=observation.year,
        text=_searchable_text(
            record,
            year=observation.year,
            value_text=value_text,
            status=observation.status,
            observation_dimensions=observation.dimensions,
        ),
        value=observation.value,
        raw_value=observation.raw_value,
        status=observation.status,
        dimensions=dimensions,
        metadata=record.provenance,
    )


def build_retrieval_units(processed_dir: Path) -> list[RetrievalUnit]:
    units: list[RetrievalUnit] = []
    evidence_ids: set[str] = set()
    for source in SOURCES:
        path = processed_dir / source.processed_filename
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise RetrievalCorpusError(f"cannot read {path}: {exc}") from exc
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                raise RetrievalCorpusError(f"blank line in {path}:{line_number}")
            try:
                record = ProcessedDemographicRecord.model_validate_json(line)
            except ValidationError as exc:
                raise RetrievalCorpusError(
                    f"invalid processed record at {path}:{line_number}: {exc}"
                ) from exc
            for observation_index in range(len(record.observations)):
                unit = retrieval_unit_from_observation(record, observation_index)
                if unit.evidence_id in evidence_ids:
                    raise RetrievalCorpusError(f"duplicate evidence ID {unit.evidence_id!r}")
                evidence_ids.add(unit.evidence_id)
                units.append(unit)
    return sorted(units, key=lambda unit: unit.evidence_id)


class TfidfRetriever:
    """Cosine TF-IDF over deterministic retrieval-unit text."""

    def __init__(self, units: Iterable[RetrievalUnit]) -> None:
        self._units = tuple(sorted(units, key=lambda unit: unit.evidence_id))
        self._term_counts = tuple(Counter(normalize_text(unit.text)) for unit in self._units)
        document_frequency: Counter[str] = Counter()
        for counts in self._term_counts:
            document_frequency.update(counts.keys())
        self._document_frequency = dict(document_frequency)
        self._document_count = len(self._units)
        self._document_norms = tuple(
            math.sqrt(
                sum(
                    (term_frequency * self._idf(token)) ** 2
                    for token, term_frequency in counts.items()
                )
            )
            for counts in self._term_counts
        )

    def _idf(self, token: str) -> float:
        frequency = self._document_frequency.get(token, 0)
        return math.log((self._document_count + 1) / (frequency + 1)) + 1.0

    def _query_vector(self, question: str) -> tuple[Counter[str], float]:
        query_counts = Counter(normalize_text(question))
        if not query_counts:
            return query_counts, 0.0
        query_norm = math.sqrt(
            sum(
                (term_frequency * self._idf(token)) ** 2
                for token, term_frequency in query_counts.items()
            )
        )
        return query_counts, query_norm

    def _score_query(
        self, query_counts: Counter[str], query_norm: float, unit_index: int
    ) -> float:
        document_norm = self._document_norms[unit_index]
        if query_norm == 0.0 or document_norm == 0.0:
            return 0.0
        document_counts = self._term_counts[unit_index]
        dot_product = sum(
            query_frequency
            * document_counts.get(token, 0)
            * self._idf(token) ** 2
            for token, query_frequency in query_counts.items()
        )
        return dot_product / (query_norm * document_norm)

    def score(self, question: str, unit_index: int) -> float:
        query_counts, query_norm = self._query_vector(question)
        return self._score_query(query_counts, query_norm, unit_index)

    def retrieve(self, question: str, k: int) -> list[RetrievalResult]:
        if k <= 0:
            raise ValueError("k must be greater than zero")
        query_counts, query_norm = self._query_vector(question)
        if not query_counts or not self._units:
            return []
        scored = [
            (self._score_query(query_counts, query_norm, index), unit)
            for index, unit in enumerate(self._units)
        ]
        scored.sort(key=lambda item: (-item[0], item[1].evidence_id))
        return [
            RetrievalResult(
                evidence=unit,
                score=score,
                rank=rank,
                retriever=RETRIEVER_NAME,
            )
            for rank, (score, unit) in enumerate(scored[: min(k, len(scored))], start=1)
        ]

    @property
    def corpus_size(self) -> int:
        return len(self._units)

    @property
    def units(self) -> Sequence[RetrievalUnit]:
        return self._units
