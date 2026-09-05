"""Integrity and freeze checks for development and held-out benchmarks."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Sequence

from app.models import BenchmarkExample, RetrievalUnit


class BenchmarkSplitError(ValueError):
    """Development/test separation or freeze metadata is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise BenchmarkSplitError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def normalise_question(question: str) -> str:
    text = unicodedata.normalize("NFKC", question).casefold()
    text = re.sub(r"['’]s\b", "", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def geography_names(units: Iterable[RetrievalUnit]) -> tuple[str, ...]:
    values = {"Singapore"}
    for unit in units:
        for key in ("planning_region", "planning_area", "subzone"):
            if key in unit.dimensions:
                values.add(unit.dimensions[key])
    return tuple(sorted(values, key=lambda value: (-len(value), value.casefold())))


def substitution_template(question: str, place_names: Sequence[str]) -> str:
    text = normalise_question(question)
    text = re.sub(r"\b(?:19|20)\d{2}\b", "<year>", text)
    for place in place_names:
        normalised_place = normalise_question(place)
        text = re.sub(
            rf"(?<!\w){re.escape(normalised_place)}(?!\w)",
            "<geography>",
            text,
        )
    return " ".join(text.split())


def validate_distinct_ids(
    development: Sequence[BenchmarkExample], heldout: Sequence[BenchmarkExample]
) -> None:
    development_ids = {example.id for example in development}
    heldout_ids = {example.id for example in heldout}
    overlap = sorted(development_ids.intersection(heldout_ids))
    if overlap:
        raise BenchmarkSplitError(
            "duplicate IDs across development and held-out sets: " + ", ".join(overlap)
        )


def analyse_cross_set_overlap(
    development: Sequence[BenchmarkExample],
    heldout: Sequence[BenchmarkExample],
    place_names: Sequence[str],
) -> dict[str, list[dict[str, str]]]:
    development_normalised: dict[str, list[str]] = defaultdict(list)
    development_templates: dict[str, list[str]] = defaultdict(list)
    development_evidence: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for example in development:
        development_normalised[normalise_question(example.question)].append(example.id)
        development_templates[substitution_template(example.question, place_names)].append(
            example.id
        )
        if example.required_evidence:
            development_evidence[tuple(sorted(example.required_evidence))].append(example.id)

    identical: list[dict[str, str]] = []
    trivial_templates: list[dict[str, str]] = []
    duplicate_evidence: list[dict[str, str]] = []
    for example in heldout:
        normalised = normalise_question(example.question)
        for development_id in development_normalised.get(normalised, []):
            identical.append({"development_id": development_id, "heldout_id": example.id})
        template = substitution_template(example.question, place_names)
        for development_id in development_templates.get(template, []):
            trivial_templates.append(
                {"development_id": development_id, "heldout_id": example.id}
            )
        if example.required_evidence:
            evidence_key = tuple(sorted(example.required_evidence))
            for development_id in development_evidence.get(evidence_key, []):
                duplicate_evidence.append(
                    {"development_id": development_id, "heldout_id": example.id}
                )
    return {
        "identical_normalised_questions": identical,
        "trivial_entity_year_templates": trivial_templates,
        "duplicate_required_evidence_sets": duplicate_evidence,
    }


def validate_internal_questions(examples: Sequence[BenchmarkExample]) -> None:
    by_question: dict[str, list[str]] = defaultdict(list)
    for example in examples:
        by_question[normalise_question(example.question)].append(example.id)
    duplicates = [ids for ids in by_question.values() if len(ids) > 1]
    if duplicates:
        raise BenchmarkSplitError(f"duplicate normalised held-out questions: {duplicates}")


def require_clean_overlap(report: dict[str, list[dict[str, str]]]) -> None:
    failures = {name: rows for name, rows in report.items() if rows}
    if failures:
        raise BenchmarkSplitError(f"development/held-out overlap detected: {failures}")


def distribution(examples: Sequence[BenchmarkExample]) -> dict[str, dict[str, int]]:
    return {
        "answerability": dict(
            sorted(Counter(example.answerability.value for example in examples).items())
        ),
        "category": dict(sorted(Counter(example.category.value for example in examples).items())),
    }


def freeze_manifest(
    *,
    benchmark_path: Path,
    seed_path: Path,
    examples: Sequence[BenchmarkExample],
    overlap_report: dict[str, list[dict[str, str]]],
) -> dict:
    return {
        "schema_version": 1,
        "status": "frozen",
        "purpose": "held_out_final_evaluation",
        "frozen_on": "2026-09-04",
        "human_reviewed": True,
        "retrieval_performance_evaluated": False,
        "development_benchmark": "evaluation/benchmark.jsonl",
        "benchmark_file": "evaluation/heldout_benchmark.jsonl",
        "benchmark_sha256": sha256_file(benchmark_path),
        "seed_file": "evaluation/heldout_benchmark_seed.jsonl",
        "seed_sha256": sha256_file(seed_path),
        "example_count": len(examples),
        "distribution": distribution(examples),
        "overlap_check_counts": {
            name: len(rows) for name, rows in sorted(overlap_report.items())
        },
        "review_note": (
            "Wording and labels were manually reviewed; numeric ground truth and "
            "evidence references were validated against the committed corpus."
        ),
    }


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def validate_freeze_manifest(
    manifest_path: Path,
    benchmark_path: Path,
    seed_path: Path,
    examples: Sequence[BenchmarkExample],
) -> dict:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkSplitError(f"invalid freeze manifest {manifest_path}: {exc}") from exc
    expected = {
        "schema_version": 1,
        "status": "frozen",
        "human_reviewed": True,
        "retrieval_performance_evaluated": False,
        "benchmark_sha256": sha256_file(benchmark_path),
        "seed_sha256": sha256_file(seed_path),
        "example_count": len(examples),
        "distribution": distribution(examples),
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise BenchmarkSplitError(
                f"freeze manifest {key!r} mismatch: {manifest.get(key)!r} != {value!r}"
            )
    return manifest
