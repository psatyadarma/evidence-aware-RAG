import json
from pathlib import Path

import pytest

from app.models import AnswerabilityClassification, BenchmarkCategory
from app.retrieval import build_retrieval_units
from evaluation.benchmark import load_benchmark, load_evidence_index, write_benchmark
from evaluation.benchmark_split import (
    BenchmarkSplitError,
    analyse_cross_set_overlap,
    geography_names,
    validate_distinct_ids,
    validate_freeze_manifest,
    validate_internal_questions,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DEVELOPMENT_PATH = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"
HELDOUT_SEED_PATH = PROJECT_ROOT / "evaluation" / "heldout_benchmark_seed.jsonl"
HELDOUT_PATH = PROJECT_ROOT / "evaluation" / "heldout_benchmark.jsonl"
FREEZE_PATH = PROJECT_ROOT / "evaluation" / "heldout_benchmark.freeze.json"


@pytest.fixture(scope="module")
def evidence_index():
    return load_evidence_index(PROCESSED_DIR)


@pytest.fixture(scope="module")
def development(evidence_index):
    return load_benchmark(DEVELOPMENT_PATH, evidence_index)


@pytest.fixture(scope="module")
def heldout(evidence_index):
    return load_benchmark(HELDOUT_PATH, evidence_index)


def test_development_and_heldout_load_separately(development, heldout) -> None:
    assert len(development) == 25
    assert len(heldout) == 45
    validate_distinct_ids(development, heldout)


def test_duplicate_ids_across_splits_are_rejected(development, heldout) -> None:
    duplicate = heldout[0].model_copy(update={"id": development[0].id})

    with pytest.raises(BenchmarkSplitError, match="duplicate IDs"):
        validate_distinct_ids(development, [duplicate, *heldout[1:]])


def test_heldout_distribution_and_schema_values(heldout) -> None:
    answerability = {
        label: sum(example.answerability == label for example in heldout)
        for label in AnswerabilityClassification
    }
    categories = {
        category: sum(example.category == category for example in heldout)
        for category in BenchmarkCategory
    }

    assert answerability == {
        AnswerabilityClassification.ANSWERABLE: 16,
        AnswerabilityClassification.PARTIALLY_ANSWERABLE: 9,
        AnswerabilityClassification.INSUFFICIENT_EVIDENCE: 14,
        AnswerabilityClassification.OUT_OF_SCOPE: 6,
    }
    assert {category for category, count in categories.items() if count} == set(
        BenchmarkCategory
    ) - {BenchmarkCategory.MULTI_HOP}


def test_heldout_seed_rebuild_is_deterministic(evidence_index, tmp_path: Path) -> None:
    examples = load_benchmark(HELDOUT_SEED_PATH, evidence_index)
    rebuilt = tmp_path / "heldout_benchmark.jsonl"
    write_benchmark(examples, rebuilt)

    assert rebuilt.read_bytes() == HELDOUT_PATH.read_bytes()


def test_supported_ground_truth_and_calculations_validate(evidence_index) -> None:
    examples = load_benchmark(HELDOUT_PATH, evidence_index)
    computed = [example for example in examples if example.computation is not None]
    direct_numeric = [
        example
        for example in examples
        if example.expected_answer is not None
        and example.expected_answer.numeric_value is not None
        and len(example.required_evidence) == 1
    ]

    assert len(computed) == 22
    assert len(direct_numeric) == 10


def test_near_duplicate_checks_are_clean(development, heldout) -> None:
    units = build_retrieval_units(PROCESSED_DIR)
    report = analyse_cross_set_overlap(development, heldout, geography_names(units))

    validate_internal_questions(heldout)
    assert report == {
        "identical_normalised_questions": [],
        "trivial_entity_year_templates": [],
        "duplicate_required_evidence_sets": [],
    }


def test_freeze_manifest_matches_files_and_distribution(heldout) -> None:
    manifest = validate_freeze_manifest(
        FREEZE_PATH,
        HELDOUT_PATH,
        HELDOUT_SEED_PATH,
        heldout,
    )

    assert manifest["status"] == "frozen"
    assert manifest["human_reviewed"] is True
    assert manifest["retrieval_performance_evaluated"] is False
    assert manifest["overlap_check_counts"] == {
        "duplicate_required_evidence_sets": 0,
        "identical_normalised_questions": 0,
        "trivial_entity_year_templates": 0,
    }


def test_checksum_validation_detects_changed_benchmark(
    heldout, tmp_path: Path
) -> None:
    changed = tmp_path / "heldout_benchmark.jsonl"
    changed.write_bytes(HELDOUT_PATH.read_bytes() + b"\n")

    with pytest.raises(BenchmarkSplitError, match="benchmark_sha256"):
        validate_freeze_manifest(
            FREEZE_PATH,
            changed,
            HELDOUT_SEED_PATH,
            heldout,
        )


def test_application_code_does_not_reference_heldout_files() -> None:
    for path in (PROJECT_ROOT / "app").glob("*.py"):
        source = path.read_text(encoding="utf-8").casefold()
        assert "heldout_benchmark" not in source
        assert "held-out benchmark" not in source


def test_retrieval_evaluation_scripts_still_default_to_development() -> None:
    scripts = (
        "evaluate_lexical_retrieval.py",
        "evaluate_semantic_retrieval.py",
        "evaluate_structured_retrieval.py",
    )
    for filename in scripts:
        source = (PROJECT_ROOT / "scripts" / filename).read_text(encoding="utf-8")
        assert '"evaluation" / "benchmark.jsonl"' in source
        assert "heldout" not in source.casefold()

    # The accepted Checkpoint 12 one-shot run now owns held-out result artifacts;
    # retrieval evaluation scripts must still remain development-only.
    assert (
        PROJECT_ROOT / "evaluation" / "results" / "final_heldout_run_completed.json"
    ).is_file()


def test_freeze_file_is_plain_reviewable_json() -> None:
    manifest = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))

    assert manifest["benchmark_file"] == "evaluation/heldout_benchmark.jsonl"
    assert len(manifest["benchmark_sha256"]) == 64
