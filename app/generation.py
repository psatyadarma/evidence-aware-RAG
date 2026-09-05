"""Grounded answer generation from deterministic facts and gate decisions."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable, Optional, Union

from pydantic import Field, ValidationError, model_validator

from app.answerability import (
    ClaimSupport,
    DecisionReason,
    DeterministicAnswerabilityDecision,
)
from app.computation import SourceQualification, VerifiedComputation, VerifiedFact
from app.llm_answerability import (
    EvidenceSufficiencyProvider,
    OutputValidationError,
    ProviderAttempt,
    ProviderCallError,
    ProviderRequest,
)
from app.models import AnswerabilityClassification, RetrievalUnit, StrictModel


PROMPT_VERSION_V1 = "grounded_answer_generation_v1"
PROMPT_VERSION = "grounded_answer_generation_v2"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_OUTPUT_TOKENS = 1000
DEFAULT_MAX_ATTEMPTS = 2


class GenerationEvidence(StrictModel):
    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    dataset_name: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    year: int
    dimensions: dict[str, str]
    value: Union[int, float]
    raw_value: Optional[str]


class UnsupportedComponent(StrictModel):
    component_id: str = Field(pattern=r"^unsupported-[0-9]{2}$")
    description: str = Field(min_length=1)
    reason_codes: list[DecisionReason] = Field(min_length=1)
    explanation: str = Field(min_length=1)


class GroundedGenerationContext(StrictModel):
    question: str = Field(min_length=1)
    gate_decision: AnswerabilityClassification
    verified_facts: list[VerifiedFact] = Field(min_length=1)
    evidence: list[GenerationEvidence] = Field(min_length=1)
    qualifications: list[SourceQualification] = Field(default_factory=list)
    unsupported_components: list[UnsupportedComponent] = Field(default_factory=list)

    @model_validator(mode="after")
    def only_generation_classes_are_allowed(self) -> "GroundedGenerationContext":
        if self.gate_decision not in {
            AnswerabilityClassification.ANSWERABLE,
            AnswerabilityClassification.PARTIALLY_ANSWERABLE,
        }:
            raise ValueError("full-abstention decisions cannot enter generation")
        if (
            self.gate_decision == AnswerabilityClassification.ANSWERABLE
            and self.unsupported_components
        ):
            raise ValueError("ANSWERABLE context cannot contain unsupported components")
        if (
            self.gate_decision == AnswerabilityClassification.PARTIALLY_ANSWERABLE
            and not self.unsupported_components
        ):
            raise ValueError("PARTIALLY_ANSWERABLE context requires unsupported components")
        return self


class GeneratedFactualClaim(StrictModel):
    text: str = Field(min_length=1)
    fact_id: str = Field(pattern=r"^fact-[0-9a-f]{16}$")
    evidence_ids: list[str] = Field(min_length=1)
    numeric_value: Optional[Union[int, float]]
    categorical_value: Optional[str]


class GroundedGeneratedAnswer(StrictModel):
    answer: str = Field(min_length=1)
    claims: list[GeneratedFactualClaim] = Field(min_length=1)
    qualification_ids: list[str]
    unsupported_component_ids_addressed: list[str]


class GenerationFailure(StrictModel):
    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)


class GroundedGenerationOutcome(StrictModel):
    answer: Optional[GroundedGeneratedAnswer] = None
    failure: Optional[GenerationFailure] = None
    context: GroundedGenerationContext
    prompt_version: str = Field(min_length=1)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
    temperature: float
    max_output_tokens: int = Field(ge=1)
    attempts: list[ProviderAttempt] = Field(min_length=1)

    @model_validator(mode="after")
    def exactly_one_result(self) -> "GroundedGenerationOutcome":
        if (self.answer is None) == (self.failure is None):
            raise ValueError("generation outcome requires exactly one of answer or failure")
        return self


SYSTEM_PROMPT_V1 = """You write a concise answer using only a deterministic evidence package.

The deterministic gate has already decided whether generation is allowed. Never change or discuss that classification.
Return only the required structured output.

Grounding rules:
1. State every verified fact in verified_facts and do not calculate any new value.
2. Every factual claim must reference exactly one supplied fact_id, copy that fact's numeric_value and categorical_value exactly, and cite exactly that fact's evidence_ids.
3. Use only supplied facts and evidence. Do not add explanations, causes, forecasts, opinions, external knowledge, or invented citations.
4. Include every supplied material qualification in the answer and list every qualification_id exactly once.
5. For PARTIALLY_ANSWERABLE, answer the supported portion, explicitly state each unsupported limitation and its reason, and list every unsupported component_id exactly once.
6. Treat NOT_AVAILABLE as unknown, never zero. Do not weaken rounding, boundary-definition, or Census-snapshot qualifications.
7. Keep the user-facing answer direct and readable. Structured claims are its citation audit trail.
"""


# The only authorized revision addresses the uniform v1 verbatim/claim-cardinality failures.
SYSTEM_PROMPT = """You write a concise answer using only a deterministic evidence package.

The deterministic gate has already decided whether generation is allowed. Never change or discuss that classification.
Return only the required structured output.

Grounding rules:
1. There is exactly one supplied verified fact. Return exactly one claim for it. Do not split or duplicate that claim.
2. Copy verified_facts[0].statement character-for-character into both claims[0].text and the user-facing answer.
3. Copy the fact_id, evidence_ids, numeric_value, and categorical_value exactly. Never calculate or alter a value.
4. Copy every supplied qualification text character-for-character into the user-facing answer, in supplied order, and list every qualification_id exactly once in supplied order.
5. For PARTIALLY_ANSWERABLE, copy every unsupported-component explanation character-for-character into the user-facing answer, in supplied order, and list every unsupported component_id exactly once in supplied order.
6. Use only supplied facts, qualifications, and limitations. Do not add explanations, causes, forecasts, opinions, external knowledge, citations, or numbers.
7. Treat NOT_AVAILABLE as unknown, never zero. Do not weaken rounding, boundary-definition, or Census-snapshot qualifications.
8. Brief connective words are allowed, but the required strings must remain verbatim. Structured claims are the citation audit trail.
"""


OUTPUT_SCHEMA = GroundedGeneratedAnswer.model_json_schema()
_PROMPT_MATERIAL = json.dumps(
    {
        "version": PROMPT_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "context_fields": list(GroundedGenerationContext.model_fields),
        "output_schema": OUTPUT_SCHEMA,
    },
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
PROMPT_HASH = hashlib.sha256(_PROMPT_MATERIAL.encode("utf-8")).hexdigest()


_PROMPT_MATERIAL_V1 = json.dumps(
    {
        "version": PROMPT_VERSION_V1,
        "system_prompt": SYSTEM_PROMPT_V1,
        "context_fields": list(GroundedGenerationContext.model_fields),
        "output_schema": OUTPUT_SCHEMA,
    },
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
PROMPT_HASH_V1 = hashlib.sha256(_PROMPT_MATERIAL_V1.encode("utf-8")).hexdigest()


_REASON_EXPLANATIONS = {
    DecisionReason.CAUSAL_EVIDENCE_MISSING: "The descriptive observations do not establish a cause.",
    DecisionReason.PREDICTION_REQUESTED: "Historical observations do not establish a forecast.",
    DecisionReason.REQUESTED_YEAR_UNAVAILABLE: "The requested year is not available at this granularity.",
    DecisionReason.REQUESTED_GEOGRAPHY_UNAVAILABLE: "The requested geography is not available at this granularity.",
    DecisionReason.VALUE_NOT_AVAILABLE: "The source publishes this value as not available, not zero.",
    DecisionReason.BOUNDARY_DEFINITION_CHANGE: "The observations cross a planning-boundary definition change.",
    DecisionReason.MISSING_REQUIRED_OPERANDS: "One or more required observations are absent.",
    DecisionReason.AMBIGUOUS_GEOGRAPHY: "The requested place does not resolve to one unambiguous geography level.",
    DecisionReason.UNSUPPORTED_ATTRIBUTE: "The requested attribute is outside the corpus.",
    DecisionReason.FALSE_PREMISE: "The supplied values contradict the question's premise.",
    DecisionReason.INCOMPATIBLE_COMPARISON: "The supplied observations are not directly compatible.",
    DecisionReason.QUERY_NOT_RESOLVED: "The requested measure or operation could not be resolved.",
}


def _unsupported_components(
    decision: DeterministicAnswerabilityDecision,
) -> list[UnsupportedComponent]:
    components = []
    for index, claim in enumerate(
        (item for item in decision.claims if item.support != ClaimSupport.SUPPORTED),
        start=1,
    ):
        explanations = [
            _REASON_EXPLANATIONS.get(reason, reason.value.replace("_", " ").lower() + ".")
            for reason in claim.reasons
        ]
        components.append(
            UnsupportedComponent(
                component_id=f"unsupported-{index:02d}",
                description=claim.description,
                reason_codes=claim.reasons,
                explanation=" ".join(explanations),
            )
        )
    return components


def build_generation_context(
    question: str,
    decision: DeterministicAnswerabilityDecision,
    computation: VerifiedComputation,
    corpus_units: Iterable[RetrievalUnit],
) -> GroundedGenerationContext:
    unit_index = {unit.evidence_id: unit for unit in corpus_units}
    evidence_ids = list(
        dict.fromkeys(
            evidence_id
            for fact in computation.facts
            for evidence_id in fact.evidence_ids
        )
    )
    missing = [item for item in evidence_ids if item not in unit_index]
    if missing:
        raise ValueError("verified evidence is absent from corpus: " + ", ".join(missing))
    evidence = [
        GenerationEvidence(
            evidence_id=unit_index[item].evidence_id,
            source_id=unit_index[item].source_id,
            dataset_name=unit_index[item].metadata.dataset_name,
            publisher=unit_index[item].metadata.publisher,
            year=unit_index[item].year,
            dimensions=dict(sorted(unit_index[item].dimensions.items())),
            value=unit_index[item].value,  # type: ignore[arg-type]
            raw_value=unit_index[item].raw_value,
        )
        for item in evidence_ids
    ]
    return GroundedGenerationContext(
        question=question,
        gate_decision=decision.classification,
        verified_facts=computation.facts,
        evidence=evidence,
        qualifications=computation.qualifications,
        unsupported_components=_unsupported_components(decision),
    )


def serialize_context(context: GroundedGenerationContext) -> str:
    return json.dumps(
        context.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_and_validate(
    output_text: str, context: GroundedGenerationContext
) -> GroundedGeneratedAnswer:
    try:
        answer = GroundedGeneratedAnswer.model_validate_json(output_text)
    except ValidationError as exc:
        raise OutputValidationError(f"schema-invalid structured output: {exc}") from exc

    facts = {fact.fact_id: fact for fact in context.verified_facts}
    if len({claim.fact_id for claim in answer.claims}) != len(answer.claims):
        raise OutputValidationError("each verified fact must appear in exactly one generated claim")
    if {claim.fact_id for claim in answer.claims} != set(facts):
        raise OutputValidationError("generated claims must cover exactly the supplied verified facts")
    for claim in answer.claims:
        fact = facts.get(claim.fact_id)
        if fact is None:  # pragma: no cover - set equality above is explicit
            raise OutputValidationError(f"unknown fact ID {claim.fact_id}")
        if claim.evidence_ids != fact.evidence_ids:
            raise OutputValidationError(f"claim {claim.fact_id} must cite its exact evidence IDs")
        if claim.text != fact.statement:
            raise OutputValidationError(f"claim {claim.fact_id} must copy the verified statement exactly")
        if claim.numeric_value != fact.numeric_value:
            raise OutputValidationError(f"claim {claim.fact_id} changed its numeric result")
        if claim.categorical_value != fact.categorical_value:
            raise OutputValidationError(f"claim {claim.fact_id} changed its categorical result")
        if fact.statement not in answer.answer:
            raise OutputValidationError(f"answer omitted verified fact {claim.fact_id}")
    expected_qualifications = [item.qualification_id for item in context.qualifications]
    if answer.qualification_ids != expected_qualifications:
        raise OutputValidationError("answer must retain every material qualification in supplied order")
    if any(item.text not in answer.answer for item in context.qualifications):
        raise OutputValidationError("answer text omitted a material qualification")
    expected_unsupported = [item.component_id for item in context.unsupported_components]
    if answer.unsupported_component_ids_addressed != expected_unsupported:
        raise OutputValidationError("answer must address every unsupported component in supplied order")
    if any(item.explanation not in answer.answer for item in context.unsupported_components):
        raise OutputValidationError("answer text omitted an unsupported-component explanation")
    allowed_numeric_text = " ".join(
        [context.question]
        + [item.statement for item in context.verified_facts]
        + [item.text for item in context.qualifications]
        + [item.explanation for item in context.unsupported_components]
    )
    number_pattern = r"(?<!\w)-?\d[\d,]*(?:\.\d+)?"
    normalise_number = lambda value: value.replace(",", "")
    allowed_numbers = {
        normalise_number(value)
        for value in re.findall(number_pattern, allowed_numeric_text)
    }
    answer_numbers = {
        normalise_number(value)
        for value in re.findall(number_pattern, answer.answer)
    }
    if not answer_numbers.issubset(allowed_numbers):
        raise OutputValidationError("answer introduced a numeric value outside the supplied package")
    return answer


class GroundedAnswerGenerator:
    """Generate from verified facts with bounded, recorded retries."""

    def __init__(
        self,
        provider: EvidenceSufficiencyProvider,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        temperature: float = DEFAULT_TEMPERATURE,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self.provider = provider
        self.max_attempts = max_attempts
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def generate(self, context: GroundedGenerationContext) -> GroundedGenerationOutcome:
        request = ProviderRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=serialize_context(context),
            output_schema=OUTPUT_SCHEMA,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
        )
        attempts: list[ProviderAttempt] = []
        final_error_type = "unknown_failure"
        final_message = "provider did not return a valid grounded answer"
        for attempt_number in range(1, self.max_attempts + 1):
            response = None
            try:
                response = self.provider.generate(request)
                answer = _parse_and_validate(response.output_text, context)
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
                return GroundedGenerationOutcome(
                    answer=answer,
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
                        model=response.model if response is not None else None,
                        response_id=response.response_id if response is not None else None,
                        input_tokens=response.input_tokens if response is not None else 0,
                        cached_input_tokens=(
                            response.cached_input_tokens if response is not None else 0
                        ),
                        output_tokens=response.output_tokens if response is not None else 0,
                        latency_ms=response.latency_ms if response is not None else 0.0,
                    )
                )
        return GroundedGenerationOutcome(
            failure=GenerationFailure(
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
