"""Provider-neutral LLM evidence-sufficiency classification.

This module deliberately has no benchmark dependency.  It packages one frozen
structured-retrieval plan, requests a small typed decision, and rejects invalid
or nonexistent evidence citations rather than coercing model output.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Protocol, Union

from pydantic import Field, ValidationError, model_validator

from app.models import (
    AnswerabilityClassification,
    ObservationStatus,
    StrictModel,
)
from app.structured_retrieval import StructuredQuery, StructuredRetriever


PROMPT_VERSION = "evidence_sufficiency_zero_shot_v1"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_OUTPUT_TOKENS = 1_000
DEFAULT_MAX_ATTEMPTS = 2


class LlmClaimSupport(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNAVAILABLE = "UNAVAILABLE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    AMBIGUOUS = "AMBIGUOUS"


class LlmReasonCode(str, Enum):
    EVIDENCE_COMPLETE = "EVIDENCE_COMPLETE"
    PARTIAL_SUPPORT = "PARTIAL_SUPPORT"
    CAUSAL_EVIDENCE_MISSING = "CAUSAL_EVIDENCE_MISSING"
    FORECAST_UNSUPPORTED = "FORECAST_UNSUPPORTED"
    REQUESTED_YEAR_UNAVAILABLE = "REQUESTED_YEAR_UNAVAILABLE"
    REQUESTED_GEOGRAPHY_UNAVAILABLE = "REQUESTED_GEOGRAPHY_UNAVAILABLE"
    VALUE_NOT_AVAILABLE = "VALUE_NOT_AVAILABLE"
    OUT_OF_DOMAIN_ATTRIBUTE = "OUT_OF_DOMAIN_ATTRIBUTE"
    FALSE_PREMISE = "FALSE_PREMISE"
    AMBIGUOUS_INTERPRETATION = "AMBIGUOUS_INTERPRETATION"
    INCOMPATIBLE_DEFINITIONS = "INCOMPATIBLE_DEFINITIONS"
    MISSING_REQUIRED_OPERANDS = "MISSING_REQUIRED_OPERANDS"
    OTHER_INSUFFICIENT_EVIDENCE = "OTHER_INSUFFICIENT_EVIDENCE"


class LlmClaimAssessment(StrictModel):
    claim: str = Field(min_length=1, max_length=250)
    support: LlmClaimSupport
    evidence_ids: list[str] = Field(max_length=30)
    reason: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def evidence_matches_support(self) -> "LlmClaimAssessment":
        if self.support == LlmClaimSupport.SUPPORTED and not self.evidence_ids:
            raise ValueError("supported claims require at least one evidence ID")
        if self.support != LlmClaimSupport.SUPPORTED and self.evidence_ids:
            raise ValueError("only supported claims may cite supporting evidence IDs")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("claim evidence IDs must be unique")
        return self


class LlmEvidenceSufficiencyDecision(StrictModel):
    classification: AnswerabilityClassification
    claims: list[LlmClaimAssessment] = Field(min_length=1, max_length=8)
    decision_evidence_ids: list[str] = Field(max_length=40)
    reason_codes: list[LlmReasonCode] = Field(min_length=1, max_length=8)
    reason: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def classification_matches_claims(self) -> "LlmEvidenceSufficiencyDecision":
        supported = sum(
            claim.support == LlmClaimSupport.SUPPORTED for claim in self.claims
        )
        if self.classification == AnswerabilityClassification.ANSWERABLE:
            if supported != len(self.claims):
                raise ValueError("ANSWERABLE requires every claim to be supported")
        elif self.classification == AnswerabilityClassification.PARTIALLY_ANSWERABLE:
            if supported == 0 or supported == len(self.claims):
                raise ValueError(
                    "PARTIALLY_ANSWERABLE requires supported and unsupported claims"
                )
        elif self.classification == AnswerabilityClassification.OUT_OF_SCOPE:
            if any(
                claim.support != LlmClaimSupport.OUT_OF_SCOPE for claim in self.claims
            ):
                raise ValueError("OUT_OF_SCOPE requires only out-of-scope claims")
        elif supported:
            raise ValueError(
                "INSUFFICIENT_EVIDENCE cannot contain supported claims"
            )
        if len(self.decision_evidence_ids) != len(set(self.decision_evidence_ids)):
            raise ValueError("decision evidence IDs must be unique")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason codes must be unique")
        claim_ids = {
            evidence_id
            for claim in self.claims
            for evidence_id in claim.evidence_ids
        }
        if not claim_ids.issubset(set(self.decision_evidence_ids)):
            raise ValueError(
                "decision evidence IDs must include all supported-claim citations"
            )
        return self


class EvidenceItem(StrictModel):
    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    dataset_name: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    year: int
    dimensions: dict[str, str]
    value: Optional[Union[int, float]] = None
    raw_value: Optional[str] = None
    status: ObservationStatus
    qualifications: list[str] = Field(default_factory=list)


class CorpusScope(StrictModel):
    source_id: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    coverage: str = Field(min_length=1)
    qualifications: list[str] = Field(default_factory=list)


class EvidenceSufficiencyContext(StrictModel):
    question: str = Field(min_length=1)
    structured_interpretation: StructuredQuery
    retrieval_diagnostics: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    corpus_scope: list[CorpusScope] = Field(min_length=1)


SYSTEM_PROMPT = """You are an evidence-sufficiency classifier for a deliberately small corpus of Singapore population statistics.

Decide whether the supplied corpus evidence is sufficient to answer every requested claim. Return only the required structured output. Provide concise claim descriptions and short justifications, never hidden chain-of-thought.

Class definitions:
- ANSWERABLE: every requested claim is explicitly supported or deterministically derivable from complete, compatible supplied observations.
- PARTIALLY_ANSWERABLE: at least one independently useful requested claim is supported and at least one is not. A single atomic trend or comparison with a missing operand is not partial.
- INSUFFICIENT_EVIDENCE: the request concerns population/demographics represented by the corpus, but no requested claim is fully supportable because evidence is missing, unavailable, ambiguous, incompatible, causal, predictive, or premise-dependent.
- OUT_OF_SCOPE: every requested primary attribute/domain is absent from the corpus. A missing row or year alone is not out of scope.

Rules:
1. Retrieved relevance is not evidence sufficiency. Check every requested claim and every required operand.
2. Do not infer facts that are not explicit in the supplied observations or deterministically derivable by lookup, comparison, sum, difference, argmax, or historical trend.
3. Descriptive observations do not establish causes. Historical observations do not justify forecasts or projections.
4. NOT_AVAILABLE and missing observations are unknown, never zero.
5. Do not accept a directional or comparative premise when the supplied values contradict it. Mark FALSE_PREMISE and do not answer the requested explanation.
6. Respect population definition, geography level, year, age, sex, reference date, rounding, and boundary qualifications. Do not silently combine incompatible definitions.
7. Treat the structured interpretation as a deterministic retrieval aid, not as ground truth. Use its diagnostics and corpus scope to identify absent or ambiguous operands.
8. Cite only exact evidence_id values supplied in evidence. Claim evidence_ids support that claim; unsupported claims must have none. decision_evidence_ids may also cite supplied observations used to detect a false premise or limitation.
9. For OUT_OF_SCOPE, all claim support values must be OUT_OF_SCOPE. Use INSUFFICIENT_EVIDENCE for in-domain missing data. Use PARTIALLY_ANSWERABLE only for explicit, independently answerable subrequests.
"""


CORPUS_SCOPE = (
    CorpusScope(
        source_id="population_indicators_annual",
        scope=(
            "National annual population indicators: total, resident, citizen, "
            "permanent-resident and non-resident population plus published growth, "
            "density, age and dependency indicators. No subnational age/sex counts."
        ),
        coverage="1950-2025; exact years vary by series",
        qualifications=[
            "Published na values are NOT_AVAILABLE, not zero.",
            "The table is descriptive and contains no causal or forecast evidence.",
        ],
    ),
    CorpusScope(
        source_id="residents_by_planning_region_annual",
        scope=(
            "Resident counts by five planning regions, published age bands and sex "
            "at end June."
        ),
        coverage="2019-2025",
        qualifications=[
            "Values are rounded to the nearest 10 and may not add exactly.",
            "2019 uses URA Master Plan 2014 boundaries; 2020 onward uses Master Plan 2019 boundaries.",
            "The table is descriptive and contains no causal or forecast evidence.",
        ],
    ),
    CorpusScope(
        source_id="resident_population_census_2020",
        scope=(
            "Resident counts by Census 2020 planning area or subzone, published age "
            "bands and sex."
        ),
        coverage="2020 snapshot only",
        qualifications=[
            "Literal '-' cells are NOT_AVAILABLE, not zero.",
            "Planning geographies use URA Master Plan 2019 boundaries.",
            "The table cannot establish changes over time, causes, or forecasts.",
        ],
    ),
)


SOURCE_QUALIFICATIONS = {
    scope.source_id: tuple(scope.qualifications) for scope in CORPUS_SCOPE
}


OUTPUT_SCHEMA = LlmEvidenceSufficiencyDecision.model_json_schema()
_PROMPT_FINGERPRINT_MATERIAL = json.dumps(
    {
        "version": PROMPT_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "user_payload_fields": list(EvidenceSufficiencyContext.model_fields),
        "output_schema": OUTPUT_SCHEMA,
    },
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
PROMPT_HASH = hashlib.sha256(
    _PROMPT_FINGERPRINT_MATERIAL.encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class ProviderRequest:
    system_prompt: str
    user_prompt: str
    output_schema: dict[str, Any]
    temperature: float
    max_output_tokens: int


@dataclass(frozen=True)
class ProviderResponse:
    output_text: str
    model: str
    response_id: Optional[str]
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    latency_ms: float


class EvidenceSufficiencyProvider(Protocol):
    provider_name: str
    requested_model: str

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Return one provider response or raise ProviderCallError."""


class ProviderCallError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class OutputValidationError(ValueError):
    """The provider returned output that cannot become a valid decision."""


class ProviderAttempt(StrictModel):
    attempt: int = Field(ge=1)
    valid: bool
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    model: Optional[str] = None
    response_id: Optional[str] = None
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)


class LlmClassificationFailure(StrictModel):
    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)


class LlmClassificationOutcome(StrictModel):
    decision: Optional[LlmEvidenceSufficiencyDecision] = None
    failure: Optional[LlmClassificationFailure] = None
    context: EvidenceSufficiencyContext
    prompt_version: str = Field(min_length=1)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
    temperature: float
    max_output_tokens: int = Field(ge=1)
    attempts: list[ProviderAttempt] = Field(min_length=1)

    @model_validator(mode="after")
    def exactly_one_result(self) -> "LlmClassificationOutcome":
        if (self.decision is None) == (self.failure is None):
            raise ValueError("outcome requires exactly one of decision or failure")
        return self


def serialize_context(context: EvidenceSufficiencyContext) -> str:
    """Canonical, byte-stable JSON supplied as the model's user message."""

    return json.dumps(
        context.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _context_for_question(
    question: str, retriever: StructuredRetriever
) -> EvidenceSufficiencyContext:
    plan = retriever.plan(question)
    evidence = [
        EvidenceItem(
            evidence_id=result.evidence.evidence_id,
            source_id=result.evidence.source_id,
            dataset_name=result.evidence.metadata.dataset_name,
            publisher=result.evidence.metadata.publisher,
            year=result.evidence.year,
            dimensions=dict(sorted(result.evidence.dimensions.items())),
            value=result.evidence.value,
            raw_value=result.evidence.raw_value,
            status=result.evidence.status,
            qualifications=list(
                SOURCE_QUALIFICATIONS.get(result.evidence.source_id, ())
            ),
        )
        for result in plan.results
    ]
    return EvidenceSufficiencyContext(
        question=question,
        structured_interpretation=plan.query,
        retrieval_diagnostics=plan.diagnostics,
        evidence=evidence,
        corpus_scope=list(CORPUS_SCOPE),
    )


def _parse_and_validate_citations(
    output_text: str, context: EvidenceSufficiencyContext
) -> LlmEvidenceSufficiencyDecision:
    try:
        decision = LlmEvidenceSufficiencyDecision.model_validate_json(output_text)
    except ValidationError as exc:
        raise OutputValidationError(f"schema-invalid structured output: {exc}") from exc

    supplied = {item.evidence_id for item in context.evidence}
    cited = set(decision.decision_evidence_ids)
    cited.update(
        evidence_id
        for claim in decision.claims
        for evidence_id in claim.evidence_ids
    )
    invalid = sorted(cited - supplied)
    if invalid:
        raise OutputValidationError(
            "output cited evidence IDs not supplied to the model: "
            + ", ".join(invalid)
        )
    return decision


class LlmEvidenceSufficiencyClassifier:
    """Assess one structured retrieval plan with a bounded retry policy."""

    def __init__(
        self,
        retriever: StructuredRetriever,
        provider: EvidenceSufficiencyProvider,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        temperature: float = DEFAULT_TEMPERATURE,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self.retriever = retriever
        self.provider = provider
        self.max_attempts = max_attempts
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def classify(self, question: str) -> LlmClassificationOutcome:
        context = _context_for_question(question, self.retriever)
        request = ProviderRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=serialize_context(context),
            output_schema=OUTPUT_SCHEMA,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
        )
        attempts: list[ProviderAttempt] = []
        final_error_type = "unknown_failure"
        final_message = "provider did not return a valid decision"

        for attempt_number in range(1, self.max_attempts + 1):
            try:
                response = self.provider.generate(request)
                decision = _parse_and_validate_citations(
                    response.output_text, context
                )
                attempts.append(
                    ProviderAttempt(
                        attempt=attempt_number,
                        valid=True,
                        model=response.model,
                        response_id=response.response_id,
                        input_tokens=response.input_tokens,
                        cached_input_tokens=response.cached_input_tokens,
                        output_tokens=response.output_tokens,
                        latency_ms=response.latency_ms,
                    )
                )
                return LlmClassificationOutcome(
                    decision=decision,
                    context=context,
                    prompt_version=PROMPT_VERSION,
                    prompt_hash=PROMPT_HASH,
                    provider=self.provider.provider_name,
                    requested_model=self.provider.requested_model,
                    temperature=self.temperature,
                    max_output_tokens=self.max_output_tokens,
                    attempts=attempts,
                )
            except ProviderCallError as exc:
                final_error_type = type(exc).__name__
                final_message = str(exc)
                attempts.append(
                    ProviderAttempt(
                        attempt=attempt_number,
                        valid=False,
                        error_type=final_error_type,
                        error_message=final_message,
                    )
                )
                if not exc.retryable:
                    break
            except OutputValidationError as exc:
                final_error_type = type(exc).__name__
                final_message = str(exc)
                attempts.append(
                    ProviderAttempt(
                        attempt=attempt_number,
                        valid=False,
                        error_type=final_error_type,
                        error_message=final_message,
                        model=response.model,
                        response_id=response.response_id,
                        input_tokens=response.input_tokens,
                        cached_input_tokens=response.cached_input_tokens,
                        output_tokens=response.output_tokens,
                        latency_ms=response.latency_ms,
                    )
                )

        return LlmClassificationOutcome(
            failure=LlmClassificationFailure(
                error_type=final_error_type,
                message=final_message,
            ),
            context=context,
            prompt_version=PROMPT_VERSION,
            prompt_hash=PROMPT_HASH,
            provider=self.provider.provider_name,
            requested_model=self.provider.requested_model,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            attempts=attempts,
        )
