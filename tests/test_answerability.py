import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.answerability import (
    ClaimSupport,
    DecisionReason,
    DeterministicAnswerabilityClassifier,
    DeterministicAnswerabilityDecision,
)
from app.models import AnswerabilityClassification, RetrievalUnit
from app.retrieval import build_retrieval_units
from app.structured_retrieval import StructuredRetriever
from evaluation.answerability_evaluation import evaluate_answerability
from evaluation.benchmark import load_benchmark, load_evidence_index


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DEVELOPMENT_PATH = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"


@pytest.fixture(scope="module")
def units() -> list[RetrievalUnit]:
    return build_retrieval_units(PROCESSED_DIR)


@pytest.fixture(scope="module")
def classifier(units: list[RetrievalUnit]) -> DeterministicAnswerabilityClassifier:
    return DeterministicAnswerabilityClassifier(StructuredRetriever(units), units)


def test_answerability_decision_schema_rejects_inconsistent_classification(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    payload = classifier.decide(
        "Please report Singapore's resident population for 2022."
    ).model_dump()
    payload["classification"] = AnswerabilityClassification.INSUFFICIENT_EVIDENCE

    with pytest.raises(ValidationError, match="cannot contain supported claims"):
        DeterministicAnswerabilityDecision.model_validate(payload)


def test_direct_answerability(classifier: DeterministicAnswerabilityClassifier) -> None:
    decision = classifier.decide(
        "Please report Singapore's resident population for 2022."
    )

    assert decision.classification == AnswerabilityClassification.ANSWERABLE
    assert decision.claims[0].support == ClaimSupport.SUPPORTED
    assert DecisionReason.EVIDENCE_COMPLETE in decision.reasons


def test_unsupported_primary_attribute_is_out_of_scope(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "What health outcomes were measured for Bedok residents during 2020?"
    )

    assert decision.classification == AnswerabilityClassification.OUT_OF_SCOPE
    assert DecisionReason.UNSUPPORTED_ATTRIBUTE in decision.reasons


def test_absent_year_is_not_replaced(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "Provide the Bedok planning-area resident count for 2016."
    )

    assert decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE
    assert DecisionReason.REQUESTED_YEAR_UNAVAILABLE in decision.reasons
    assert decision.retrieved_evidence_ids == []


def test_absent_geography_level_is_exposed(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "Provide the resident count for the Simei planning area during 2020."
    )

    assert decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE
    assert DecisionReason.REQUESTED_GEOGRAPHY_UNAVAILABLE in decision.reasons


def test_published_unavailable_value_is_distinct_from_absence(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "Return the all-sex, all-age Census 2020 resident count for Simpang planning area."
    )

    assert decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE
    assert DecisionReason.VALUE_NOT_AVAILABLE in decision.reasons
    assert len(decision.retrieved_evidence_ids) == 1


def test_causal_request_remains_unsupported_with_descriptive_rows(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "What reasons explain the change in Singapore's citizen population from 2017 to 2018?"
    )

    assert decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE
    assert DecisionReason.CAUSAL_EVIDENCE_MISSING in decision.reasons
    assert len(decision.retrieved_evidence_ids) == 2


def test_forecast_is_not_extrapolated(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide("Predict Singapore's total population for 2030.")

    assert decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE
    assert DecisionReason.PREDICTION_REQUESTED in decision.reasons


def test_independent_value_plus_cause_is_partial(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "Report Singapore's resident population in 2022, and explain why it changed from 2021."
    )

    assert decision.classification == AnswerabilityClassification.PARTIALLY_ANSWERABLE
    assert {claim.support for claim in decision.claims} == {
        ClaimSupport.SUPPORTED,
        ClaimSupport.UNSUPPORTED,
    }
    assert DecisionReason.PARTIAL_SUPPORT in decision.reasons


def test_historical_value_plus_forecast_is_partial(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "Report Singapore's total population in 2025, and forecast its value for 2030."
    )

    assert decision.classification == AnswerabilityClassification.PARTIALLY_ANSWERABLE
    assert DecisionReason.PREDICTION_REQUESTED in decision.reasons


def test_population_value_plus_external_attribute_is_partial(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "Give Bedok's resident total for 2020, and what was its unemployment rate?"
    )

    assert decision.classification == AnswerabilityClassification.PARTIALLY_ANSWERABLE
    assert DecisionReason.UNSUPPORTED_ATTRIBUTE in decision.reasons


@pytest.mark.parametrize(
    "question",
    [
        "Compare Singapore's non-resident population in 2021 with 2022.",
        "How many male East Region residents were aged 10–19 in 2023?",
        "Which planning region recorded the highest resident count during 2023?",
        "How did Singapore's citizen population change from 2010 to 2012?",
    ],
)
def test_supported_comparison_aggregation_argmax_and_trend(
    classifier: DeterministicAnswerabilityClassifier, question: str
) -> None:
    assert classifier.decide(question).classification == AnswerabilityClassification.ANSWERABLE


def test_false_temporal_premise_is_detected(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "Why did Singapore's total population fall from 2024 to 2025?"
    )

    assert decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE
    assert DecisionReason.FALSE_PREMISE in decision.reasons
    assert decision.premise_check is not None
    assert decision.premise_check.contradicted is True


def test_material_geography_ambiguity_is_insufficient(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "Give the Census 2020 resident total for Central Water Catchment."
    )

    assert decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE
    assert DecisionReason.AMBIGUOUS_GEOGRAPHY in decision.reasons


def test_cross_level_ambiguous_comparison_is_incompatible(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "Did Central Water Catchment have more residents than Straits View in Census 2020?"
    )

    assert decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE
    assert DecisionReason.INCOMPATIBLE_COMPARISON in decision.reasons


def test_publishable_boundary_crossing_is_answerable_with_caveat(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "What numerical change appears in the North Region resident totals between 2019 and 2021?"
    )

    assert decision.classification == AnswerabilityClassification.ANSWERABLE
    assert DecisionReason.BOUNDARY_DEFINITION_CHANGE in decision.reasons


def test_unqualified_underlying_boundary_change_is_partial(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "State the North Region resident totals in 2019 and 2021, and how much did its underlying population change?"
    )

    assert decision.classification == AnswerabilityClassification.PARTIALLY_ANSWERABLE
    assert DecisionReason.BOUNDARY_DEFINITION_CHANGE in decision.reasons


def test_incomplete_atomic_area_trend_is_not_partial(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    decision = classifier.decide(
        "Trace Jurong West's planning-area resident total every year from 2019 to 2022."
    )

    assert decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE
    assert not any(claim.support == ClaimSupport.SUPPORTED for claim in decision.claims)


def test_decisions_are_repeatable_and_preserve_evidence_ids(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    question = "Compare Singapore's non-resident population in 2021 with 2022."
    first = classifier.decide(question)
    second = classifier.decide(question)

    assert first == second
    assert first.retrieved_evidence_ids == [
        "population_indicators_annual:r5:y2021",
        "population_indicators_annual:r5:y2022",
    ]


def test_development_evaluation_has_expected_deterministic_metrics(
    classifier: DeterministicAnswerabilityClassifier,
) -> None:
    examples = load_benchmark(
        DEVELOPMENT_PATH,
        load_evidence_index(PROCESSED_DIR),
    )
    report, summary = evaluate_answerability(examples, classifier)

    assert len(report) == 25
    assert summary["evaluation_split"] == "development"
    assert summary["accuracy"] == 1.0
    assert summary["macro_f1"] == 1.0
    assert summary["abstention"]["precision"] == 1.0
    assert summary["abstention"]["recall"] == 1.0
    assert summary["errors"] == []


def test_answerability_implementation_is_isolated_from_ground_truth() -> None:
    path = PROJECT_ROOT / "app" / "answerability.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert "evaluation" not in imported_roots
    assert "benchmark" not in source.casefold()
    assert "heldout" not in source.casefold()
    assert re.search(r"\b[qt]\d{3}\b", source, flags=re.IGNORECASE) is None
    for forbidden in ("expected_answer", "required_evidence", "BenchmarkExample"):
        assert forbidden not in source


def test_answerability_runner_names_development_input_only() -> None:
    source = (PROJECT_ROOT / "scripts" / "evaluate_answerability.py").read_text(
        encoding="utf-8"
    )

    assert '"evaluation" / "benchmark.jsonl"' in source
    assert "heldout" not in source.casefold()
