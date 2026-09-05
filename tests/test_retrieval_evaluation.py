from datetime import date

from app.models import (
    AnswerabilityClassification,
    BenchmarkCategory,
    BenchmarkExample,
    DocumentMetadata,
    ExpectedAnswer,
    ObservationStatus,
    RequiredClaim,
    RetrievalUnit,
    TemporalCoverage,
)
from app.retrieval import TfidfRetriever
from evaluation.retrieval_evaluation import evaluate_retrieval


def _unit(evidence_id: str, text: str) -> RetrievalUnit:
    metadata = DocumentMetadata(
        source_url="https://data.gov.sg/datasets/example/view",
        dataset_name="Example",
        publisher="SingStat",
        licence="Singapore Open Data Licence 1.0",
        retrieved_at=date(2026, 9, 3),
        temporal_coverage=TemporalCoverage(start="2025", end="2025"),
    )
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
        dimensions={"series": text},
        metadata=metadata,
    )


def test_only_answerable_and_partial_examples_enter_positive_metrics() -> None:
    answerable = BenchmarkExample(
        id="a",
        question="alpha",
        category=BenchmarkCategory.DIRECT_LOOKUP,
        answerability=AnswerabilityClassification.ANSWERABLE,
        expected_answer=ExpectedAnswer(canonical="Alpha", numeric_value=1),
        required_evidence=["alpha"],
        claims=[RequiredClaim(claim="Alpha", supported=True, evidence_ids=["alpha"])],
    )
    insufficient = BenchmarkExample(
        id="i",
        question="alpha why",
        category=BenchmarkCategory.UNSUPPORTED_CAUSALITY,
        answerability=AnswerabilityClassification.INSUFFICIENT_EVIDENCE,
        required_evidence=["alpha"],
        claims=[RequiredClaim(claim="Cause", supported=False)],
    )
    out_of_scope = BenchmarkExample(
        id="o",
        question="income",
        category=BenchmarkCategory.OUT_OF_SCOPE,
        answerability=AnswerabilityClassification.OUT_OF_SCOPE,
        claims=[RequiredClaim(claim="Income", supported=False)],
    )
    retriever = TfidfRetriever([_unit("alpha", "alpha"), _unit("beta", "beta")])

    report, summary = evaluate_retrieval(
        [answerable, insufficient, out_of_scope], retriever, ks=(1,)
    )

    assert summary["positive_evidence_examples"] == 1
    assert summary["excluded_from_positive_metrics"] == 2
    assert summary["overall_mean_recall_at_k"]["1"] == 1.0
    assert report[0]["positive_evidence_evaluation"] is True
    assert report[1]["positive_evidence_evaluation"] is False
    assert report[1]["recall_at_k"]["1"] is None
    assert report[2]["recall_at_k"]["1"] is None
