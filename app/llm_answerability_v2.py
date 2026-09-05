"""Checkpoint 9 v2 claim assessment with deterministic label derivation.

V1 remains unchanged in ``app.llm_answerability``.  This version separates
requested claims from premises and gives every evidence reference an explicit
semantic relation before deriving the existing four-way answerability label.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional

from pydantic import Field, ValidationError, model_validator

from app.llm_answerability import (
    CORPUS_SCOPE,
    SOURCE_QUALIFICATIONS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TEMPERATURE,
    EvidenceItem,
    EvidenceSufficiencyContext,
    EvidenceSufficiencyProvider,
    LlmClassificationFailure,
    LlmReasonCode,
    OutputValidationError,
    ProviderAttempt,
    ProviderCallError,
    ProviderRequest,
    serialize_context,
)
from app.models import AnswerabilityClassification, StrictModel
from app.structured_retrieval import StructuredRetriever


PROMPT_VERSION_V2 = "evidence_sufficiency_zero_shot_v2"


class EvidenceRelation(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    ESTABLISHES_UNAVAILABILITY = "ESTABLISHES_UNAVAILABILITY"
    CONTEXT = "CONTEXT"


class RequestedClaimSupport(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNAVAILABLE = "UNAVAILABLE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    AMBIGUOUS = "AMBIGUOUS"


class PremiseStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"


class RelatedEvidence(StrictModel):
    evidence_id: str = Field(min_length=1)
    relation: EvidenceRelation


class RequestedClaimAssessmentV2(StrictModel):
    claim: str = Field(min_length=1, max_length=250)
    support: RequestedClaimSupport
    evidence: list[RelatedEvidence] = Field(max_length=40)
    reason: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def relations_match_support(self) -> "RequestedClaimAssessmentV2":
        if len({item.evidence_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("claim evidence IDs must be unique")
        relations = {item.relation for item in self.evidence}
        if self.support == RequestedClaimSupport.SUPPORTED:
            if EvidenceRelation.SUPPORTS not in relations:
                raise ValueError("SUPPORTED claims require SUPPORTS evidence")
        elif EvidenceRelation.SUPPORTS in relations:
            raise ValueError("only SUPPORTED claims may use SUPPORTS evidence")
        if self.support == RequestedClaimSupport.UNAVAILABLE:
            if EvidenceRelation.ESTABLISHES_UNAVAILABILITY not in relations:
                raise ValueError(
                    "UNAVAILABLE claims require evidence establishing unavailability"
                )
        elif EvidenceRelation.ESTABLISHES_UNAVAILABILITY in relations:
            raise ValueError(
                "ESTABLISHES_UNAVAILABILITY requires an UNAVAILABLE claim"
            )
        if self.support == RequestedClaimSupport.OUT_OF_SCOPE and any(
            relation != EvidenceRelation.CONTEXT for relation in relations
        ):
            raise ValueError("OUT_OF_SCOPE claims may reference CONTEXT only")
        return self


class PremiseAssessmentV2(StrictModel):
    premise: str = Field(min_length=1, max_length=250)
    status: PremiseStatus
    evidence: list[RelatedEvidence] = Field(max_length=40)
    reason: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def relations_match_status(self) -> "PremiseAssessmentV2":
        if len({item.evidence_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("premise evidence IDs must be unique")
        relations = {item.relation for item in self.evidence}
        if self.status == PremiseStatus.SUPPORTED:
            if EvidenceRelation.SUPPORTS not in relations:
                raise ValueError("SUPPORTED premises require SUPPORTS evidence")
            if EvidenceRelation.CONTRADICTS in relations:
                raise ValueError("SUPPORTED premises cannot use CONTRADICTS evidence")
        elif self.status == PremiseStatus.CONTRADICTED:
            if EvidenceRelation.CONTRADICTS not in relations:
                raise ValueError("CONTRADICTED premises require CONTRADICTS evidence")
            if EvidenceRelation.SUPPORTS in relations:
                raise ValueError("CONTRADICTED premises cannot use SUPPORTS evidence")
        elif relations & {EvidenceRelation.SUPPORTS, EvidenceRelation.CONTRADICTS}:
            raise ValueError(
                "UNKNOWN premises may reference CONTEXT or unavailability only"
            )
        return self


class LlmEvidenceAssessmentV2(StrictModel):
    requested_claims: list[RequestedClaimAssessmentV2] = Field(
        min_length=1, max_length=8
    )
    premises: list[PremiseAssessmentV2] = Field(max_length=8)
    reason_codes: list[LlmReasonCode] = Field(min_length=1, max_length=8)
    summary: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def unique_reason_codes(self) -> "LlmEvidenceAssessmentV2":
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason codes must be unique")
        return self


def derive_answerability(
    assessment: LlmEvidenceAssessmentV2,
) -> AnswerabilityClassification:
    """Apply the frozen benchmark semantics to model-assessed requested claims."""

    supports = [
        claim.support == RequestedClaimSupport.SUPPORTED
        for claim in assessment.requested_claims
    ]
    if all(supports):
        return AnswerabilityClassification.ANSWERABLE
    if len(supports) > 1 and any(supports):
        return AnswerabilityClassification.PARTIALLY_ANSWERABLE
    if all(
        claim.support == RequestedClaimSupport.OUT_OF_SCOPE
        for claim in assessment.requested_claims
    ):
        return AnswerabilityClassification.OUT_OF_SCOPE
    return AnswerabilityClassification.INSUFFICIENT_EVIDENCE


class DerivedEvidenceSufficiencyDecisionV2(StrictModel):
    classification: AnswerabilityClassification
    assessment: LlmEvidenceAssessmentV2

    @model_validator(mode="after")
    def classification_is_derived(self) -> "DerivedEvidenceSufficiencyDecisionV2":
        expected = derive_answerability(self.assessment)
        if self.classification != expected:
            raise ValueError(
                f"classification must be deterministically derived as {expected.value}"
            )
        return self


SYSTEM_PROMPT_V2 = """You assess evidence sufficiency for a deliberately small corpus of Singapore population statistics.

Return only the required structured requested-claim and premise assessments. Do not choose an overall answerability class: application code derives it deterministically. Provide concise reasons, never hidden or long-form chain-of-thought.

Assess only materially requested claims: facts or conclusions that must be established to satisfy the user's request. Keep descriptive premises or background context separate from requested claims. For example, when a user asks for a cause, the cause is the requested claim; an asserted direction of change is a premise. A supported premise does not make a pure causal request partially answerable.

Requested-claim support states:
- SUPPORTED: the supplied observations explicitly establish or deterministically derive the requested claim by lookup, comparison, sum, difference, argmax, or historical trend.
- UNSUPPORTED: the claim is in domain, but the supplied descriptive evidence cannot establish it, including causes, forecasts, contradicted-premise conclusions, missing operands, or incompatible definitions.
- UNAVAILABLE: a supplied observation explicitly marks the requested value NOT_AVAILABLE.
- OUT_OF_SCOPE: the requested attribute/domain is absent from the selected corpus. A missing year or row alone is not out of scope.
- AMBIGUOUS: a material interpretation or geography ambiguity prevents one defensible requested claim from being established.

Premise statuses are SUPPORTED, CONTRADICTED, or UNKNOWN. Premises do not count as requested claims when application code derives answerability.

Every evidence reference requires one relation:
- SUPPORTS: directly supports or is a required operand for the assessed claim or premise.
- CONTRADICTS: supplied observations contradict an asserted premise.
- ESTABLISHES_UNAVAILABILITY: a supplied NOT_AVAILABLE observation establishes that no numeric value is published.
- CONTEXT: relevant descriptive context that does not support the requested conclusion, such as before/after observations accompanying an unsupported causal claim.

Relation rules:
1. Use only exact evidence_id values supplied in evidence.
2. Every SUPPORTED requested claim requires SUPPORTS evidence. Unsupported, unavailable, out-of-scope, or ambiguous claims must not use SUPPORTS.
3. UNSUPPORTED claims may cite CONTRADICTS or CONTEXT. UNAVAILABLE claims must cite ESTABLISHES_UNAVAILABILITY.
4. A CONTRADICTED premise must cite CONTRADICTS evidence. A SUPPORTED premise must cite SUPPORTS evidence.

Sufficiency rules:
1. Retrieved relevance is not sufficiency. Check every requested claim and every operand.
2. Descriptive observations do not establish causality. Historical observations do not justify forecasting.
3. NOT_AVAILABLE and missing observations are unknown, never zero.
4. Do not accept a directional or comparative premise when supplied values contradict it.
5. Respect population definition, geography level, year, age, sex, reference date, rounding, and every supplied source qualification. Evidence can be numerically present yet insufficient for an unqualified real-world claim when definitions or boundaries differ.
6. If a request separately asks for published values or arithmetic and for a stronger conclusion that a qualification prevents, represent those as separate requested claims. Do not treat descriptive context as a supported requested component unless the user actually requested it.
7. Treat the structured interpretation as a deterministic retrieval aid, not ground truth. Its diagnostics and corpus scope can establish missing or ambiguous operands.
"""


OUTPUT_SCHEMA_V2 = LlmEvidenceAssessmentV2.model_json_schema()
_PROMPT_FINGERPRINT_MATERIAL_V2 = json.dumps(
    {
        "version": PROMPT_VERSION_V2,
        "system_prompt": SYSTEM_PROMPT_V2,
        "user_payload_fields": list(EvidenceSufficiencyContext.model_fields),
        "output_schema": OUTPUT_SCHEMA_V2,
    },
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
PROMPT_HASH_V2 = hashlib.sha256(
    _PROMPT_FINGERPRINT_MATERIAL_V2.encode("utf-8")
).hexdigest()


class LlmClassificationOutcomeV2(StrictModel):
    decision: Optional[DerivedEvidenceSufficiencyDecisionV2] = None
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
    def exactly_one_result(self) -> "LlmClassificationOutcomeV2":
        if (self.decision is None) == (self.failure is None):
            raise ValueError("outcome requires exactly one of decision or failure")
        return self


def _context_for_question_v2(
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


def _parse_and_validate_v2(
    output_text: str, context: EvidenceSufficiencyContext
) -> DerivedEvidenceSufficiencyDecisionV2:
    try:
        assessment = LlmEvidenceAssessmentV2.model_validate_json(output_text)
    except ValidationError as exc:
        raise OutputValidationError(f"schema-invalid structured output: {exc}") from exc

    supplied = {item.evidence_id for item in context.evidence}
    cited = {
        item.evidence_id
        for claim in assessment.requested_claims
        for item in claim.evidence
    }
    cited.update(
        item.evidence_id
        for premise in assessment.premises
        for item in premise.evidence
    )
    invalid = sorted(cited - supplied)
    if invalid:
        raise OutputValidationError(
            "output cited evidence IDs not supplied to the model: "
            + ", ".join(invalid)
        )
    return DerivedEvidenceSufficiencyDecisionV2(
        classification=derive_answerability(assessment),
        assessment=assessment,
    )


class LlmEvidenceSufficiencyClassifierV2:
    """Assess claims once per attempt, then derive the final class in code."""

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

    def classify(self, question: str) -> LlmClassificationOutcomeV2:
        context = _context_for_question_v2(question, self.retriever)
        request = ProviderRequest(
            system_prompt=SYSTEM_PROMPT_V2,
            user_prompt=serialize_context(context),
            output_schema=OUTPUT_SCHEMA_V2,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
        )
        attempts: list[ProviderAttempt] = []
        final_error_type = "unknown_failure"
        final_message = "provider did not return a valid claim assessment"

        for attempt_number in range(1, self.max_attempts + 1):
            try:
                response = self.provider.generate(request)
                decision = _parse_and_validate_v2(response.output_text, context)
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
                return LlmClassificationOutcomeV2(
                    decision=decision,
                    context=context,
                    prompt_version=PROMPT_VERSION_V2,
                    prompt_hash=PROMPT_HASH_V2,
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

        return LlmClassificationOutcomeV2(
            failure=LlmClassificationFailure(
                error_type=final_error_type,
                message=final_message,
            ),
            context=context,
            prompt_version=PROMPT_VERSION_V2,
            prompt_hash=PROMPT_HASH_V2,
            provider=self.provider.provider_name,
            requested_model=self.provider.requested_model,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            attempts=attempts,
        )
