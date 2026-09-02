from datetime import date

import pytest
from pydantic import ValidationError

from app.models import (
    AnswerabilityClassification,
    AnswerabilityDecision,
    BenchmarkCategory,
    BenchmarkExample,
    Document,
    DocumentMetadata,
    Evidence,
    GeneratedAnswer,
    RequiredClaim,
    RetrievalResult,
    TemporalCoverage,
)


@pytest.fixture
def metadata() -> DocumentMetadata:
    return DocumentMetadata(
        source_url="https://data.gov.sg/example",
        dataset_name="Example population dataset",
        publisher="Singapore Department of Statistics",
        licence="Singapore Open Data Licence",
        retrieved_at=date(2026, 9, 3),
        temporal_coverage=TemporalCoverage(start="2020", end="2025"),
    )


def test_document_and_retrieval_result_round_trip(metadata: DocumentMetadata) -> None:
    document = Document(document_id="doc-1", content="Population: 100", metadata=metadata)
    evidence = Evidence(
        evidence_id="evidence-1",
        document_id=document.document_id,
        text=document.content,
        metadata=document.metadata,
        location="row 1",
    )
    result = RetrievalResult(
        evidence=evidence,
        score=-3.25,
        rank=1,
        retriever="example-distance-retriever",
    )

    assert RetrievalResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("score", [float("inf"), float("-inf"), float("nan")])
def test_retrieval_score_must_be_finite(metadata: DocumentMetadata, score: float) -> None:
    evidence = Evidence(
        evidence_id="evidence-1",
        document_id="doc-1",
        text="Population: 100",
        metadata=metadata,
    )

    with pytest.raises(ValidationError, match="finite"):
        RetrievalResult(evidence=evidence, score=score, rank=1, retriever="test")


def test_supported_claim_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="at least one evidence"):
        RequiredClaim(claim="Tampines population was X", supported=True)


def test_partial_decision_requires_mixed_claim_support() -> None:
    decision = AnswerabilityDecision(
        classification=AnswerabilityClassification.PARTIALLY_ANSWERABLE,
        confidence=0.91,
        required_claims=[
            RequiredClaim(
                claim="Tampines population changed",
                supported=True,
                evidence_ids=["doc-12"],
            ),
            RequiredClaim(claim="The cause of the change", supported=False),
        ],
        reason="The values are present but no cause is documented.",
    )

    assert decision.classification is AnswerabilityClassification.PARTIALLY_ANSWERABLE


def test_answerable_decision_rejects_unsupported_claim() -> None:
    with pytest.raises(ValidationError, match="fully supported"):
        AnswerabilityDecision(
            classification=AnswerabilityClassification.ANSWERABLE,
            confidence=0.8,
            required_claims=[RequiredClaim(claim="Unknown cause", supported=False)],
            reason="Invalid example.",
        )


def test_abstention_cannot_include_answer() -> None:
    with pytest.raises(ValidationError, match="abstention"):
        GeneratedAnswer(answer="An unsupported answer", abstained=True)


def test_answerable_benchmark_requires_expected_answer() -> None:
    with pytest.raises(ValidationError, match="expected answer"):
        BenchmarkExample(
            id="q001",
            question="What was the population?",
            category=BenchmarkCategory.DIRECT_LOOKUP,
            answerability=AnswerabilityClassification.ANSWERABLE,
        )


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        TemporalCoverage(start="2020", undocumented_field="hidden heuristic")

