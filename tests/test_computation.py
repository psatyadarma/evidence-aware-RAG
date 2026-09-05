import ast
from pathlib import Path

import pytest

from app.answerability import DeterministicAnswerabilityClassifier
from app.computation import ComputationError, DeterministicComputationEngine
from app.models import AnswerabilityClassification
from app.retrieval import build_retrieval_units
from app.structured_retrieval import StructuredRetriever
from evaluation.benchmark import load_benchmark, load_evidence_index


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


@pytest.fixture(scope="module")
def system():
    units = build_retrieval_units(PROCESSED_DIR)
    retriever = StructuredRetriever(units)
    classifier = DeterministicAnswerabilityClassifier(retriever, units)
    return units, classifier, DeterministicComputationEngine(units)


@pytest.fixture(scope="module")
def examples():
    return load_benchmark(
        PROJECT_ROOT / "evaluation" / "benchmark.jsonl",
        load_evidence_index(PROCESSED_DIR),
    )


def _by_id(examples, example_id):
    return next(item for item in examples if item.id == example_id)


@pytest.mark.parametrize(
    ("example_id", "expected"),
    [
        ("q001", 6111175),
        ("q004", -1099),
        ("q006", 38290),
        ("q007", 214820),
        ("q008", 5650),
        ("q009", 160305),
        ("q010", 50430),
        ("q011", 23647),
        ("q012", 259900),
        ("q013", 1200),
    ],
)
def test_deterministic_numeric_results(system, examples, example_id, expected):
    _, classifier, engine = system
    decision = classifier.decide(_by_id(examples, example_id).question)

    result = engine.compute(decision)

    assert result.facts[0].numeric_value == expected


def test_argmax_uses_only_supplied_candidates(system, examples):
    _, classifier, engine = system
    decision = classifier.decide(_by_id(examples, "q005").question)

    fact = engine.compute(decision).facts[0]

    assert fact.result_evidence_id == "residents_by_planning_region_annual:r1:y2025"
    assert len(fact.evidence_ids) == 5


def test_comparison_operation_is_supported(system):
    _, classifier, engine = system
    decision = classifier.decide(
        "Were there more female or male residents in Tampines in Census 2020?"
    )

    fact = engine.compute(decision).facts[0]

    assert fact.kind.value == "comparison"
    assert fact.categorical_value in {"first", "second", "equal"}
    assert len(fact.operands) == 2


def test_partial_independent_clause_uses_lookup(system, examples):
    _, classifier, engine = system
    decision = classifier.decide(_by_id(examples, "q012").question)

    result = engine.compute(decision)

    assert decision.classification == AnswerabilityClassification.PARTIALLY_ANSWERABLE
    assert result.operation.value == "lookup"
    assert len(result.facts[0].evidence_ids) == 1


def test_material_qualifications_are_selected_generically(system, examples):
    _, classifier, engine = system
    census = engine.compute(classifier.decide(_by_id(examples, "q002").question))
    boundary = engine.compute(classifier.decide(_by_id(examples, "q013").question))

    assert [item.qualification_id for item in census.qualifications] == [
        "census-2020-snapshot-mp2019"
    ]
    assert [item.qualification_id for item in boundary.qualifications] == [
        "planning-region-rounded-nearest-10",
        "planning-region-boundary-change-2019-2020",
    ]


def test_full_abstention_has_no_supported_computation(system, examples):
    _, classifier, engine = system
    decision = classifier.decide(_by_id(examples, "q025").question)

    with pytest.raises(ComputationError, match="no gate-supported evidence"):
        engine.compute(decision)


def test_computation_runtime_has_no_benchmark_dependency():
    source = (PROJECT_ROOT / "app" / "computation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "evaluation" not in imports
    assert "expected_answer" not in source
    assert "BenchmarkExample" not in source
