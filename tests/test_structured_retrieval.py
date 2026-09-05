import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import ObservationStatus, RetrievalUnit
from app.retrieval import build_retrieval_units
from app.structured_retrieval import (
    AgeConstraint,
    AgeConstraintKind,
    GeographyLevel,
    MeasureStatus,
    StructuredOperation,
    StructuredQuery,
    StructuredRetriever,
    parse_published_age_band,
)
from evaluation.benchmark import load_benchmark, load_evidence_index
from evaluation.structured_retrieval_evaluation import evaluate_structured_retrieval


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def units() -> list[RetrievalUnit]:
    return build_retrieval_units(PROJECT_ROOT / "data" / "processed")


@pytest.fixture(scope="module")
def retriever(units: list[RetrievalUnit]) -> StructuredRetriever:
    return StructuredRetriever(units)


def test_structured_query_validation(retriever: StructuredRetriever) -> None:
    payload = retriever.interpreter.interpret(
        "Give the national resident population for 2022."
    ).model_dump()
    payload["measure_status"] = MeasureStatus.UNSUPPORTED
    payload["unsupported_attribute"] = "income"

    with pytest.raises(ValidationError, match="requires only unsupported_attribute"):
        StructuredQuery.model_validate(payload)


def test_age_constraint_validation() -> None:
    with pytest.raises(ValidationError, match="maximum cannot precede minimum"):
        AgeConstraint(kind=AgeConstraintKind.RANGE, minimum_age=40, maximum_age=20)


def test_year_and_multi_year_extraction(retriever: StructuredRetriever) -> None:
    single = retriever.interpreter.interpret(
        "Give Singapore's resident population during 2022."
    )
    endpoints = retriever.interpreter.interpret(
        "Compare Singapore's resident population between 2019 and 2023."
    )
    annual = retriever.interpreter.interpret(
        "List Bedok resident counts every year from 2019 to 2022."
    )

    assert single.years == [2022]
    assert endpoints.years == [2019, 2023]
    assert annual.years == [2019, 2020, 2021, 2022]


def test_known_geography_and_explicit_level_resolution(
    retriever: StructuredRetriever,
) -> None:
    inferred = retriever.interpreter.interpret(
        "Give Yishun's resident count during 2020."
    )
    explicit = retriever.interpreter.interpret(
        "Give Simei subzone's female resident count during 2020."
    )

    assert inferred.geography_level == GeographyLevel.PLANNING_AREA
    assert inferred.geographies[0].entity == "Yishun"
    assert explicit.geography_level == GeographyLevel.SUBZONE
    assert explicit.geographies[0].planning_area == "Tampines"


def test_ambiguous_geography_is_not_silently_resolved(
    retriever: StructuredRetriever,
) -> None:
    query = retriever.interpreter.interpret(
        "Give the resident count for Changi Bay during 2020."
    )

    assert query.geography_ambiguous is True
    assert query.geography_level is None
    assert {item.level for item in query.geographies} == {
        GeographyLevel.PLANNING_AREA,
        GeographyLevel.SUBZONE,
    }


def test_published_age_band_parsing() -> None:
    closed = parse_published_age_band("25 - 29 Years")
    open_ended = parse_published_age_band("90 Years & Over")

    assert (closed.minimum_age, closed.maximum_age) == (25, 29)
    assert (open_ended.minimum_age, open_ended.maximum_age) == (90, None)


@pytest.mark.parametrize(
    "phrase",
    ["65+", "65 years and over", "at least 65"],
)
def test_older_age_normalisations_use_actual_bands(
    retriever: StructuredRetriever, phrase: str
) -> None:
    query = retriever.interpreter.interpret(
        f"How many North Region residents were aged {phrase} during 2023?"
    )

    assert query.age_constraint == AgeConstraint(
        kind=AgeConstraintKind.AT_LEAST, minimum_age=65
    )
    assert query.resolved_age_groups == [
        "65 - 69 Years",
        "70 - 74 Years",
        "75 - 79 Years",
        "80 - 84 Years",
        "85 - 89 Years",
        "90 Years & Over",
    ]


def test_non_aligned_age_predicate_is_marked_inexact(
    retriever: StructuredRetriever,
) -> None:
    query = retriever.interpreter.interpret(
        "How many East Region residents were aged 67+ during 2023?"
    )

    assert query.resolved_age_groups[0] == "70 - 74 Years"
    assert "age_constraint_not_exactly_representable" in query.diagnostics


def test_sex_and_measure_normalisation(retriever: StructuredRetriever) -> None:
    query = retriever.interpreter.interpret(
        "Compare men and women among Yishun residents during 2020."
    )

    assert query.requested_measure == "Resident Population"
    assert query.sexes == ["Male", "Female"]
    assert query.evidence_operation == StructuredOperation.COMPARISON


def test_series_resolution_does_not_confuse_resident_types(
    retriever: StructuredRetriever,
) -> None:
    permanent = retriever.interpreter.interpret(
        "State Singapore's permanent resident population during 2023."
    )
    resident = retriever.interpreter.interpret(
        "State Singapore's resident population during 2023."
    )

    assert permanent.requested_measure == "Permanent Resident Population"
    assert resident.requested_measure == "Resident Population"


def test_omitted_dimensions_default_to_published_totals(
    retriever: StructuredRetriever,
) -> None:
    query = retriever.interpreter.interpret(
        "Give Bedok's census resident count during 2020."
    )

    assert query.resolved_age_groups == ["Total"]
    assert query.sexes == ["Total"]
    assert query.defaulted_age_to_total is True
    assert query.defaulted_sex_to_total is True


def test_operation_recognition(retriever: StructuredRetriever) -> None:
    maximum = retriever.interpreter.interpret(
        "Which planning region recorded the highest resident population during 2024?"
    )
    causal = retriever.interpreter.interpret(
        "What caused Singapore's resident population to change from 2022 to 2023?"
    )

    assert maximum.operation == StructuredOperation.ARGMAX
    assert causal.operation == StructuredOperation.CAUSAL_EXPLANATION
    assert causal.evidence_operation == StructuredOperation.DIFFERENCE
    assert "causal_explanation_not_supported_by_descriptive_observations" in causal.diagnostics


def test_exact_filtering_and_evidence_identity(retriever: StructuredRetriever) -> None:
    result = retriever.retrieve(
        "Give the female resident count aged 90+ for Ang Mo Kio during 2020.", 5
    )

    assert len(result) == 1
    assert result[0].evidence.evidence_id == (
        "resident_population_census_2020:r2:y2020:age_group=90-years-over:sex=female"
    )
    assert result[0].evidence.dimensions["geography_level"] == "planning_area"


def test_multi_evidence_and_deterministic_ordering(
    retriever: StructuredRetriever,
) -> None:
    question = "Compare Yishun with Tampines resident totals during 2020."
    first = retriever.retrieve_all(question)
    second = retriever.retrieve_all(question)

    assert first == second
    assert len(first) == 2
    assert [item.evidence.dimensions["planning_area"] for item in first] == [
        "Yishun",
        "Tampines",
    ]
    assert [item.rank for item in first] == [1, 2]


def test_argmax_returns_every_candidate_without_computing_winner(
    retriever: StructuredRetriever,
) -> None:
    results = retriever.retrieve_all(
        "Which planning region had the most residents during 2023?"
    )

    assert len(results) == 5
    assert {item.evidence.dimensions["planning_region"] for item in results} == {
        "Central Region",
        "East Region",
        "North Region",
        "North-East Region",
        "West Region",
    }


def test_unknown_attribute_and_absent_year_are_clean_diagnostics(
    retriever: StructuredRetriever,
) -> None:
    unsupported = retriever.plan("Describe health outcomes in Bedok during 2020.")
    absent = retriever.plan("Give Yishun's resident population during 2017.")

    assert unsupported.query.measure_status == MeasureStatus.UNSUPPORTED
    assert unsupported.query.unsupported_attribute == "health"
    assert unsupported.results == []
    assert absent.results == []
    assert "no_exact_schema_match" in absent.diagnostics


def test_explicit_geography_level_mismatch_returns_nothing(
    retriever: StructuredRetriever,
) -> None:
    plan = retriever.plan(
        "Give the resident count for the Simei planning area during 2020."
    )

    assert plan.results == []
    assert "geography_level_name_mismatch" in plan.diagnostics


def test_provenance_and_unavailable_status_are_preserved(
    retriever: StructuredRetriever,
) -> None:
    results = retriever.retrieve_all(
        "Give the resident count for the Changi Bay planning area during 2020."
    )

    assert len(results) == 1
    evidence = results[0].evidence
    assert evidence.status == ObservationStatus.NOT_AVAILABLE
    assert evidence.metadata.publisher == "Singapore Department of Statistics (SingStat)"
    assert evidence.source_id == "resident_population_census_2020"


def test_retrieval_code_has_no_ground_truth_dependency() -> None:
    modules = (
        PROJECT_ROOT / "app" / "retrieval.py",
        PROJECT_ROOT / "app" / "semantic_retrieval.py",
        PROJECT_ROOT / "app" / "structured_retrieval.py",
    )
    for path in modules:
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
        assert re_search_question_id(source) is False
        for forbidden in ("expected_answer", "required_evidence", "BenchmarkExample"):
            assert forbidden not in source


def test_fixed_evaluation_and_null_unsupported_metrics(
    retriever: StructuredRetriever,
) -> None:
    benchmark = load_benchmark(
        PROJECT_ROOT / "evaluation" / "benchmark.jsonl",
        load_evidence_index(PROJECT_ROOT / "data" / "processed"),
    )
    report, summary = evaluate_structured_retrieval(benchmark, retriever)

    assert summary["overall_mean_recall_at_k"] == {
        "1": pytest.approx(0.6051282051282052),
        "3": pytest.approx(0.9307692307692308),
        "5": pytest.approx(0.9871794871794871),
        "10": 1.0,
    }
    assert summary["overall_full_required_evidence_recovery"] == 1.0
    assert all(
        row["full_required_evidence_recovery"] == 1.0
        for row in report
        if row["positive_evidence_evaluation"]
    )
    assert all(
        all(value is None for value in row["recall_at_k"].values())
        for row in report
        if not row["positive_evidence_evaluation"]
    )


def re_search_question_id(source: str) -> bool:
    import re

    return re.search(r"\bq\d{3}\b", source, flags=re.IGNORECASE) is not None
