import inspect
import math
from datetime import date
from pathlib import Path

import pytest

import app.retrieval as retrieval_module
from app.models import (
    DocumentMetadata,
    ObservationStatus,
    RetrievalUnit,
    TemporalCoverage,
)
from app.retrieval import TfidfRetriever, build_retrieval_units, normalize_text


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


@pytest.fixture(scope="module")
def corpus_units() -> list[RetrievalUnit]:
    return build_retrieval_units(PROCESSED_DIR)


@pytest.fixture
def metadata() -> DocumentMetadata:
    return DocumentMetadata(
        source_url="https://data.gov.sg/datasets/example/view",
        dataset_name="Example dataset",
        publisher="Singapore Department of Statistics (SingStat)",
        licence="Singapore Open Data Licence 1.0",
        retrieved_at=date(2026, 9, 3),
        temporal_coverage=TemporalCoverage(start="2020", end="2025"),
    )


def unit(evidence_id: str, text: str, metadata: DocumentMetadata) -> RetrievalUnit:
    return RetrievalUnit(
        evidence_id=evidence_id,
        document_id="example:r1",
        source_id="example",
        record_id=1,
        year=2025,
        text=text,
        value=1,
        raw_value="1",
        status=ObservationStatus.OBSERVED,
        dimensions={"series": "Example"},
        metadata=metadata,
    )


def test_normalization_is_explicit_and_preserves_numbers() -> None:
    assert normalize_text("Singapore’s RESIDENTS, ages 65–69 in 2,025 & years") == (
        "singapore",
        "resident",
        "age",
        "65",
        "69",
        "in",
        "2025",
        "and",
        "year",
    )
    assert normalize_text("43.2") == ("43.2",)


def test_retrieval_unit_count_and_stable_order(corpus_units: list[RetrievalUnit]) -> None:
    assert len(corpus_units) == 27584
    assert [item.evidence_id for item in corpus_units] == sorted(
        item.evidence_id for item in corpus_units
    )
    assert len({item.evidence_id for item in corpus_units}) == len(corpus_units)


def test_retrieval_unit_preserves_id_dimensions_and_provenance(
    corpus_units: list[RetrievalUnit],
) -> None:
    evidence_id = "resident_population_census_2020:r323:y2020:age_group=total:sex=total"
    item = next(unit for unit in corpus_units if unit.evidence_id == evidence_id)

    assert item.document_id == "resident_population_census_2020:r323"
    assert item.year == 2020
    assert item.value == 259900
    assert item.dimensions["planning_area"] == "Tampines"
    assert item.dimensions["sex"] == "Total"
    assert item.metadata.dataset_name.startswith("Resident Population by Planning Area")
    assert "Planning area: Tampines." in item.text
    assert "Published value: 259900." in item.text


def test_unavailable_observation_remains_searchable(corpus_units: list[RetrievalUnit]) -> None:
    evidence_id = "resident_population_census_2020:r84:y2020:age_group=total:sex=total"
    item = next(unit for unit in corpus_units if unit.evidence_id == evidence_id)

    assert item.status is ObservationStatus.NOT_AVAILABLE
    assert item.value is None
    assert "Published value: not available." in item.text
    assert "Source marker: -." in item.text


def test_tfidf_score_matches_documented_formula(metadata: DocumentMetadata) -> None:
    retriever = TfidfRetriever(
        [unit("a", "alpha beta", metadata), unit("b", "alpha gamma", metadata)]
    )
    idf_beta = math.log(3 / 2) + 1
    expected = idf_beta / math.sqrt(1 + idf_beta**2)

    assert retriever.score("beta", 0) == pytest.approx(expected)
    assert retriever.score("beta", 1) == 0.0


def test_ties_break_by_evidence_id(metadata: DocumentMetadata) -> None:
    retriever = TfidfRetriever(
        [unit("b", "alpha gamma", metadata), unit("a", "alpha beta", metadata)]
    )

    results = retriever.retrieve("alpha", 2)

    assert [result.evidence.evidence_id for result in results] == ["a", "b"]
    assert results[0].score == pytest.approx(results[1].score)


def test_empty_query_returns_no_results(metadata: DocumentMetadata) -> None:
    retriever = TfidfRetriever([unit("a", "alpha", metadata)])

    assert retriever.retrieve(" -- ", 1) == []


@pytest.mark.parametrize("k", [0, -1])
def test_non_positive_k_is_rejected(metadata: DocumentMetadata, k: int) -> None:
    retriever = TfidfRetriever([unit("a", "alpha", metadata)])

    with pytest.raises(ValueError, match="greater than zero"):
        retriever.retrieve("alpha", k)


def test_k_larger_than_corpus_and_repeated_calls_are_deterministic(
    metadata: DocumentMetadata,
) -> None:
    retriever = TfidfRetriever(
        [unit("a", "alpha beta", metadata), unit("b", "gamma", metadata)]
    )

    first = retriever.retrieve("alpha", 99)
    second = retriever.retrieve("alpha", 99)

    assert len(first) == 2
    assert first == second
    assert [result.rank for result in first] == [1, 2]


def test_retrieval_module_has_no_benchmark_dependency() -> None:
    source = inspect.getsource(retrieval_module)

    assert "evaluation" not in source
    assert "benchmark" not in source

