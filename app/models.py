"""Typed contracts shared by retrieval, answerability, generation, and evaluation."""

from __future__ import annotations

import math
from datetime import date
from enum import Enum
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and normalises surrounding whitespace."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AnswerabilityClassification(str, Enum):
    ANSWERABLE = "ANSWERABLE"
    PARTIALLY_ANSWERABLE = "PARTIALLY_ANSWERABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class BenchmarkCategory(str, Enum):
    DIRECT_LOOKUP = "direct_lookup"
    AGGREGATION = "aggregation"
    COMPARISON = "comparison"
    DESCRIPTIVE_TREND = "descriptive_trend"
    MULTI_HOP = "multi_hop"
    PARTIALLY_ANSWERABLE = "partially_answerable"
    UNSUPPORTED_CAUSALITY = "unsupported_causality"
    FALSE_PREMISE = "false_premise"
    TEMPORAL_MISMATCH = "temporal_mismatch"
    GEOGRAPHIC_MISMATCH = "geographic_mismatch"
    UNAVAILABLE_VALUE = "unavailable_value"
    OUT_OF_SCOPE = "out_of_scope"


class ComputationType(str, Enum):
    DIFFERENCE = "difference"
    SUM = "sum"
    COMPARISON = "comparison"
    ARGMAX = "argmax"


class TemporalCoverage(StrictModel):
    """Human-readable coverage retained without assuming a source-specific date format."""

    start: Optional[str] = Field(default=None, min_length=1)
    end: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_coverage_value(self) -> "TemporalCoverage":
        if not any((self.start, self.end, self.description)):
            raise ValueError("temporal coverage must contain start, end, or description")
        return self


class DocumentMetadata(StrictModel):
    source_url: HttpUrl
    dataset_name: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    licence: Optional[str] = Field(default=None, min_length=1)
    retrieved_at: date
    temporal_coverage: TemporalCoverage


class Document(StrictModel):
    document_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    metadata: DocumentMetadata


class Evidence(StrictModel):
    """A retrievable passage with provenance back to its source document."""

    evidence_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    metadata: DocumentMetadata
    location: Optional[str] = Field(default=None, min_length=1)


class ObservationStatus(str, Enum):
    OBSERVED = "OBSERVED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class PopulationObservation(StrictModel):
    """One published annual value, retaining the upstream representation."""

    year: int = Field(ge=1900, le=2100)
    value: Optional[Union[int, float]] = None
    raw_value: Optional[str] = None
    status: ObservationStatus
    dimensions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def value_matches_status(self) -> "PopulationObservation":
        if self.status == ObservationStatus.OBSERVED and self.value is None:
            raise ValueError("OBSERVED requires a numeric value")
        if self.status == ObservationStatus.NOT_AVAILABLE and self.value is not None:
            raise ValueError("NOT_AVAILABLE cannot contain a numeric value")
        return self


class ProcessedDemographicRecord(StrictModel):
    """Normalized, source-row-level representation used before retrieval exists."""

    source_id: str = Field(min_length=1)
    record_id: int = Field(ge=1)
    dimensions: dict[str, str]
    observations: list[PopulationObservation] = Field(min_length=1)
    provenance: DocumentMetadata


class RetrievalUnit(StrictModel):
    """One searchable corpus observation with provenance and explicit dimensions."""

    evidence_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    record_id: int = Field(ge=1)
    year: int = Field(ge=1900, le=2100)
    text: str = Field(min_length=1)
    value: Optional[Union[int, float]] = None
    raw_value: Optional[str] = None
    status: ObservationStatus
    dimensions: dict[str, str]
    metadata: DocumentMetadata


class RetrievalResult(StrictModel):
    """A ranked result. Score is finite but intentionally not forced to [0, 1]."""

    evidence: RetrievalUnit
    score: float
    rank: int = Field(ge=1)
    retriever: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_finite_score(self) -> "RetrievalResult":
        if not math.isfinite(self.score):
            raise ValueError("retrieval score must be finite")
        return self


class RequiredClaim(StrictModel):
    claim: str = Field(min_length=1)
    supported: bool
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_matches_support(self) -> "RequiredClaim":
        if self.supported and not self.evidence_ids:
            raise ValueError("a supported claim must cite at least one evidence ID")
        if not self.supported and self.evidence_ids:
            raise ValueError("an unsupported claim cannot cite supporting evidence IDs")
        return self


class AnswerabilityDecision(StrictModel):
    classification: AnswerabilityClassification
    confidence: float = Field(ge=0.0, le=1.0)
    required_claims: list[RequiredClaim] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def classification_matches_claim_support(self) -> "AnswerabilityDecision":
        supported = sum(claim.supported for claim in self.required_claims)
        total = len(self.required_claims)

        if self.classification == AnswerabilityClassification.ANSWERABLE:
            if total == 0 or supported != total:
                raise ValueError("ANSWERABLE requires one or more fully supported claims")
        elif self.classification == AnswerabilityClassification.PARTIALLY_ANSWERABLE:
            if total < 2 or supported == 0 or supported == total:
                raise ValueError("PARTIALLY_ANSWERABLE requires supported and unsupported claims")
        elif supported:
            raise ValueError(
                "INSUFFICIENT_EVIDENCE and OUT_OF_SCOPE cannot contain supported claims"
            )
        return self


class Citation(StrictModel):
    evidence_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)


class GeneratedAnswer(StrictModel):
    answer: Optional[str] = Field(default=None, min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    abstained: bool

    @model_validator(mode="after")
    def answer_matches_abstention(self) -> "GeneratedAnswer":
        if self.abstained and (self.answer is not None or self.citations):
            raise ValueError("an abstention cannot contain an answer or citations")
        if not self.abstained and self.answer is None:
            raise ValueError("a non-abstaining response must contain an answer")
        return self


class ExpectedAnswer(StrictModel):
    canonical: str = Field(min_length=1)
    numeric_value: Optional[Union[int, float]] = None
    numeric_tolerance: Optional[float] = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def tolerance_requires_numeric_value(self) -> "ExpectedAnswer":
        if self.numeric_tolerance is not None and self.numeric_value is None:
            raise ValueError("numeric_tolerance requires numeric_value")
        if self.numeric_value is not None and not math.isfinite(self.numeric_value):
            raise ValueError("numeric_value must be finite")
        return self


class BenchmarkComputation(StrictModel):
    type: ComputationType
    evidence_ids: list[str] = Field(min_length=1)
    result: Union[int, float, str]
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def numeric_result_must_be_finite(self) -> "BenchmarkComputation":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("computation evidence IDs must be unique")
        if isinstance(self.result, (int, float)) and not math.isfinite(self.result):
            raise ValueError("numeric computation result must be finite")
        return self


class EvidenceTarget(StrictModel):
    """A corpus observation selector expected to be absent for a boundary case."""

    source_id: str = Field(min_length=1)
    year: int = Field(ge=1900, le=2100)
    record_dimensions: dict[str, str]
    observation_dimensions: dict[str, str] = Field(default_factory=dict)


class BenchmarkExample(StrictModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    category: BenchmarkCategory
    answerability: AnswerabilityClassification
    expected_answer: Optional[ExpectedAnswer] = None
    required_evidence: list[str] = Field(default_factory=list)
    claims: list[RequiredClaim] = Field(default_factory=list)
    requires_computation: bool = False
    computation: Optional[BenchmarkComputation] = None
    unavailable_targets: list[EvidenceTarget] = Field(default_factory=list)
    notes: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def ground_truth_is_consistent(self) -> "BenchmarkExample":
        if len(self.required_evidence) != len(set(self.required_evidence)):
            raise ValueError("required_evidence IDs must be unique")
        evidence_set = set(self.required_evidence)
        claim_evidence = {
            evidence_id
            for claim in self.claims
            for evidence_id in claim.evidence_ids
        }
        if not claim_evidence.issubset(evidence_set):
            raise ValueError("claim evidence must be included in required_evidence")

        if self.answerability == AnswerabilityClassification.ANSWERABLE:
            if self.expected_answer is None:
                raise ValueError("ANSWERABLE benchmark examples require an expected answer")
            if not self.required_evidence:
                raise ValueError("ANSWERABLE benchmark examples require evidence")
            if not self.claims or not all(claim.supported for claim in self.claims):
                raise ValueError("ANSWERABLE examples require fully supported claims")
            if claim_evidence != evidence_set:
                raise ValueError("ANSWERABLE required evidence must support its claims")
        elif self.answerability == AnswerabilityClassification.PARTIALLY_ANSWERABLE:
            supported = sum(claim.supported for claim in self.claims)
            if self.expected_answer is None or not self.required_evidence:
                raise ValueError("PARTIALLY_ANSWERABLE requires an answer and evidence")
            if len(self.claims) < 2 or supported == 0 or supported == len(self.claims):
                raise ValueError("PARTIALLY_ANSWERABLE requires mixed claim support")
            if claim_evidence != evidence_set:
                raise ValueError("PARTIALLY_ANSWERABLE evidence must support its claims")
        elif self.expected_answer is not None:
            raise ValueError("full-abstention examples cannot have an expected answer")

        if (
            self.answerability == AnswerabilityClassification.OUT_OF_SCOPE
            and self.required_evidence
        ):
            raise ValueError("OUT_OF_SCOPE examples cannot require corpus evidence")
        if self.category in {
            BenchmarkCategory.TEMPORAL_MISMATCH,
            BenchmarkCategory.GEOGRAPHIC_MISMATCH,
        } and not self.unavailable_targets:
            raise ValueError("temporal/geographic mismatch requires an unavailable target")

        if self.requires_computation != (self.computation is not None):
            raise ValueError("requires_computation must match computation presence")
        if self.computation is not None:
            if not set(self.computation.evidence_ids).issubset(evidence_set):
                raise ValueError("computation evidence must be included in required_evidence")
        return self


class SystemPrediction(StrictModel):
    """Provider-neutral prediction contract for future system evaluation."""

    id: str = Field(min_length=1)
    predicted_answerability: AnswerabilityClassification
    abstained: bool
    predicted_answer: Optional[str] = Field(default=None, min_length=1)
    numeric_answer: Optional[Union[int, float]] = None
    retrieved_evidence_ids: list[str] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def response_matches_abstention(self) -> "SystemPrediction":
        if self.abstained and self.numeric_answer is not None:
            raise ValueError("an abstention cannot include a numeric answer")
        if not self.abstained and self.predicted_answer is None:
            raise ValueError("a substantive prediction requires predicted_answer")
        if self.numeric_answer is not None and not math.isfinite(self.numeric_answer):
            raise ValueError("numeric_answer must be finite")
        return self
