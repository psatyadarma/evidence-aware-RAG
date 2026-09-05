import json
from pathlib import Path

import pytest

from app.models import AnswerabilityClassification, BenchmarkCategory
from evaluation.benchmark import (
    BenchmarkValidationError,
    load_benchmark,
    load_evidence_index,
    write_benchmark,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
CURATED_SEED = PROJECT_ROOT / "evaluation" / "benchmark_seed.jsonl"
BUILT_BENCHMARK = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"


@pytest.fixture(scope="module")
def evidence_index():
    return load_evidence_index(PROCESSED_DIR)


def _seed_objects() -> list[dict]:
    return [json.loads(line) for line in CURATED_SEED.read_text(encoding="utf-8").splitlines()]


def _write_objects(path: Path, objects: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in objects),
        encoding="utf-8",
    )


def test_curated_benchmark_loads_and_has_reviewed_size(evidence_index) -> None:
    examples = load_benchmark(CURATED_SEED, evidence_index)

    assert len(examples) == 25
    assert {example.category for example in examples} == {
        BenchmarkCategory.DIRECT_LOOKUP,
        BenchmarkCategory.COMPARISON,
        BenchmarkCategory.AGGREGATION,
        BenchmarkCategory.DESCRIPTIVE_TREND,
        BenchmarkCategory.PARTIALLY_ANSWERABLE,
        BenchmarkCategory.UNSUPPORTED_CAUSALITY,
        BenchmarkCategory.TEMPORAL_MISMATCH,
        BenchmarkCategory.GEOGRAPHIC_MISMATCH,
        BenchmarkCategory.UNAVAILABLE_VALUE,
        BenchmarkCategory.FALSE_PREMISE,
        BenchmarkCategory.OUT_OF_SCOPE,
    }


def test_built_benchmark_is_deterministic(evidence_index, tmp_path: Path) -> None:
    examples = load_benchmark(CURATED_SEED, evidence_index)
    rebuilt = tmp_path / "benchmark.jsonl"
    write_benchmark(examples, rebuilt)

    assert rebuilt.read_bytes() == BUILT_BENCHMARK.read_bytes()


def test_duplicate_ids_are_rejected(evidence_index, tmp_path: Path) -> None:
    objects = _seed_objects()[:2]
    objects[1]["id"] = objects[0]["id"]
    path = tmp_path / "duplicate.jsonl"
    _write_objects(path, objects)

    with pytest.raises(BenchmarkValidationError, match="duplicate benchmark ID"):
        load_benchmark(path, evidence_index)


def test_unknown_category_is_rejected(evidence_index, tmp_path: Path) -> None:
    objects = _seed_objects()[:1]
    objects[0]["category"] = "invented_category"
    path = tmp_path / "category.jsonl"
    _write_objects(path, objects)

    with pytest.raises(BenchmarkValidationError, match="invalid benchmark example"):
        load_benchmark(path, evidence_index)


def test_unknown_answerability_label_is_rejected(evidence_index, tmp_path: Path) -> None:
    objects = _seed_objects()[:1]
    objects[0]["answerability"] = "MAYBE"
    path = tmp_path / "answerability.jsonl"
    _write_objects(path, objects)

    with pytest.raises(BenchmarkValidationError, match="invalid benchmark example"):
        load_benchmark(path, evidence_index)


def test_nonexistent_evidence_is_rejected(evidence_index, tmp_path: Path) -> None:
    objects = _seed_objects()[:1]
    missing = "population_indicators_annual:r999:y2025"
    objects[0]["required_evidence"] = [missing]
    objects[0]["claims"][0]["evidence_ids"] = [missing]
    path = tmp_path / "missing-evidence.jsonl"
    _write_objects(path, objects)

    with pytest.raises(BenchmarkValidationError, match="nonexistent required evidence"):
        load_benchmark(path, evidence_index)


def test_incorrect_lookup_answer_is_rejected(evidence_index, tmp_path: Path) -> None:
    objects = _seed_objects()[:1]
    objects[0]["expected_answer"]["numeric_value"] = 1
    path = tmp_path / "wrong-lookup.jsonl"
    _write_objects(path, objects)

    with pytest.raises(BenchmarkValidationError, match="does not match source observation"):
        load_benchmark(path, evidence_index)


def test_incorrect_computation_is_rejected(evidence_index, tmp_path: Path) -> None:
    objects = [_seed_objects()[3]]
    objects[0]["computation"]["result"] = -1000
    objects[0]["expected_answer"]["numeric_value"] = -1000
    path = tmp_path / "wrong-computation.jsonl"
    _write_objects(path, objects)

    with pytest.raises(BenchmarkValidationError, match="does not match calculated result"):
        load_benchmark(path, evidence_index)


def test_partial_example_has_supported_and_unsupported_claims(evidence_index) -> None:
    examples = load_benchmark(CURATED_SEED, evidence_index)
    example = next(item for item in examples if item.id == "q011")

    assert example.answerability is AnswerabilityClassification.PARTIALLY_ANSWERABLE
    assert [claim.supported for claim in example.claims] == [True, False]
    assert example.expected_answer is not None
    assert "does not establish why" in example.expected_answer.canonical


def test_unavailable_target_must_remain_absent(evidence_index, tmp_path: Path) -> None:
    objects = [_seed_objects()[16]]
    objects[0]["unavailable_targets"][0]["year"] = 2020
    path = tmp_path / "available-target.jsonl"
    _write_objects(path, objects)

    with pytest.raises(BenchmarkValidationError, match="marked unavailable now resolves"):
        load_benchmark(path, evidence_index)


def test_unsupported_computation_type_is_rejected(evidence_index, tmp_path: Path) -> None:
    objects = [_seed_objects()[3]]
    objects[0]["computation"]["type"] = "division"
    path = tmp_path / "unsupported-computation.jsonl"
    _write_objects(path, objects)

    with pytest.raises(BenchmarkValidationError, match="invalid benchmark example"):
        load_benchmark(path, evidence_index)


def test_out_of_scope_example_cannot_reference_corpus(evidence_index, tmp_path: Path) -> None:
    objects = [_seed_objects()[22]]
    objects[0]["required_evidence"] = ["population_indicators_annual:r1:y2025"]
    path = tmp_path / "out-of-scope-evidence.jsonl"
    _write_objects(path, objects)

    with pytest.raises(BenchmarkValidationError, match="cannot require corpus evidence"):
        load_benchmark(path, evidence_index)
