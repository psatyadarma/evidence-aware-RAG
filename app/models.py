"""Typed contracts shared by retrieval, answerability, generation, and evaluation."""

from __future__ import annotations

import math
from datetime import date
from enum import Enum
from typing import Optional

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
    MULTI_HOP = "multi_hop"
    PARTIALLY_ANSWERABLE = "partially_answerable"
    UNSUPPORTED_CAUSALITY = "unsupported_causality"
    FALSE_PREMISE = "false_premise"
    TEMPORAL_MISMATCH = "temporal_mismatch"
    OUT_OF_SCOPE = "out_of_scope"


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


class RetrievalResult(StrictModel):
    """A ranked result. Score is finite but intentionally not forced to [0, 1]."""

    evidence: Evidence
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


class BenchmarkExample(StrictModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    category: BenchmarkCategory
    answerability: AnswerabilityClassification
    expected_answer: Optional[str] = Field(default=None, min_length=1)
    required_sources: list[str] = Field(default_factory=list)
    notes: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def expected_answer_matches_label(self) -> "BenchmarkExample":
        if (
            self.answerability == AnswerabilityClassification.ANSWERABLE
            and self.expected_answer is None
        ):
            raise ValueError("ANSWERABLE benchmark examples require an expected answer")
        if self.answerability in {
            AnswerabilityClassification.INSUFFICIENT_EVIDENCE,
            AnswerabilityClassification.OUT_OF_SCOPE,
        } and self.expected_answer is not None:
            raise ValueError("abstention-labelled examples cannot have an expected answer")
        return self

