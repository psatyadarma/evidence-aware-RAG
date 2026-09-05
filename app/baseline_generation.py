"""Shared baseline generator for ungated and similarity-threshold RAG."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional, Sequence, Union

from pydantic import Field, ValidationError, model_validator

from app.llm_answerability import (
    EvidenceSufficiencyProvider,
    OutputValidationError,
    ProviderAttempt,
    ProviderCallError,
    ProviderRequest,
)
from app.models import ObservationStatus, RetrievalResult, StrictModel


PROMPT_VERSION = "baseline_structured_evidence_generation_v1"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_OUTPUT_TOKENS = 1000
DEFAULT_MAX_ATTEMPTS = 2


SOURCE_QUALIFICATIONS = {
    "population_indicators_annual": [
        "Published na values are NOT_AVAILABLE, not zero.",
        "The table is descriptive and contains no causal or forecast evidence.",
    ],
    "residents_by_planning_region_annual": [
        "Values are rounded to the nearest 10 and may not add exactly.",
        "2019 uses URA Master Plan 2014 boundaries; 2020 onward uses Master Plan 2019 boundaries.",
        "The table is descriptive and contains no causal or forecast evidence.",
    ],
    "resident_population_census_2020": [
        "Literal '-' cells are NOT_AVAILABLE, not zero.",
        "Planning geographies use URA Master Plan 2019 boundaries.",
        "The table cannot establish changes over time, causes, or forecasts.",
    ],
}


class BaselineAction(str, Enum):
    ANSWER = "ANSWER"
    ABSTAIN = "ABSTAIN"


class BaselineEvidence(StrictModel):
    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    year: int
    dimensions: dict[str, str]
    value: Optional[Union[int, float]]
    raw_value: Optional[str]
    status: ObservationStatus
    qualifications: list[str]


class BaselineGenerationContext(StrictModel):
    question: str = Field(min_length=1)
    evidence: list[BaselineEvidence]


class BaselineClaim(StrictModel):
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    numeric_value: Optional[Union[int, float]]
    categorical_value: Optional[str]


class BaselineGeneratedAnswer(StrictModel):
    action: BaselineAction
    answer: Optional[str]
    claims: list[BaselineClaim]
    limitations: list[str]

    @model_validator(mode="after")
    def action_matches_payload(self) -> "BaselineGeneratedAnswer":
        if self.action == BaselineAction.ANSWER:
            if self.answer is None or not self.claims:
                raise ValueError("ANSWER requires answer text and at least one cited claim")
        elif self.answer is not None or self.claims or not self.limitations:
            raise ValueError("ABSTAIN requires null answer, no claims, and a limitation")
        return self


class BaselineFailure(StrictModel):
    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)


class BaselineGenerationOutcome(StrictModel):
    result: Optional[BaselineGeneratedAnswer] = None
    failure: Optional[BaselineFailure] = None
    context: BaselineGenerationContext
    prompt_version: str = Field(min_length=1)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
    temperature: float
    max_output_tokens: int = Field(ge=1)
    attempts: list[ProviderAttempt] = Field(min_length=1)

    @model_validator(mode="after")
    def exactly_one_result(self) -> "BaselineGenerationOutcome":
        if (self.result is None) == (self.failure is None):
            raise ValueError("outcome requires exactly one result or failure")
        return self


SYSTEM_PROMPT = """You are the baseline generator for a small corpus of Singapore population statistics.

Use only the supplied structured-retrieval evidence and source qualifications. Return only the required structured output.

Choose ANSWER only when the supplied observations support a substantive response to the request. You may perform transparent lookup, sum, difference, comparison, argmax, or descriptive historical-trend arithmetic from supplied numeric values. Choose ABSTAIN when the needed evidence is absent, NOT_AVAILABLE, ambiguous, incompatible, causal, predictive, outside the corpus, or otherwise insufficient. Do not force an answer.

Rules:
1. Do not use outside knowledge, invent evidence, infer a missing value as zero, make a causal explanation from descriptive data, or forecast beyond supplied years.
2. Preserve material rounding, geography-boundary, Census-snapshot, and NOT_AVAILABLE qualifications.
3. ANSWER may include a supported partial response, but must state the unsupported portion in limitations and must not answer that portion.
4. Every factual claim must cite only exact evidence_id values supplied in the request. Put a main numeric or categorical result in its claim field when one is stated; otherwise use null.
5. ABSTAIN must have null answer, no claims, and at least one concise limitation. Do not cite evidence as if it supported an unavailable conclusion.
6. Keep the answer and claim descriptions concise. Never reveal hidden reasoning.
"""


OUTPUT_SCHEMA = BaselineGeneratedAnswer.model_json_schema()
_PROMPT_MATERIAL = json.dumps(
    {
        "version": PROMPT_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "context_fields": list(BaselineGenerationContext.model_fields),
        "output_schema": OUTPUT_SCHEMA,
    },
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
PROMPT_HASH = hashlib.sha256(_PROMPT_MATERIAL.encode("utf-8")).hexdigest()


def build_baseline_context(
    question: str, results: Sequence[RetrievalResult]
) -> BaselineGenerationContext:
    return BaselineGenerationContext(
        question=question,
        evidence=[
            BaselineEvidence(
                evidence_id=item.evidence.evidence_id,
                source_id=item.evidence.source_id,
                text=item.evidence.text,
                year=item.evidence.year,
                dimensions=dict(sorted(item.evidence.dimensions.items())),
                value=item.evidence.value,
                raw_value=item.evidence.raw_value,
                status=item.evidence.status,
                qualifications=SOURCE_QUALIFICATIONS.get(item.evidence.source_id, []),
            )
            for item in results
        ],
    )


def serialize_context(context: BaselineGenerationContext) -> str:
    return json.dumps(
        context.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_and_validate(
    output_text: str, context: BaselineGenerationContext
) -> BaselineGeneratedAnswer:
    try:
        answer = BaselineGeneratedAnswer.model_validate_json(output_text)
    except ValidationError as exc:
        raise OutputValidationError(f"schema-invalid baseline output: {exc}") from exc
    supplied = {item.evidence_id for item in context.evidence}
    cited = {
        evidence_id for claim in answer.claims for evidence_id in claim.evidence_ids
    }
    invalid = sorted(cited - supplied)
    if invalid:
        raise OutputValidationError(
            "baseline output cited evidence IDs not supplied: " + ", ".join(invalid)
        )
    return answer


class BaselineAnswerGenerator:
    """Shared bounded generator; contains no gate, benchmark, or verified facts."""

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

    def generate(
        self, context: BaselineGenerationContext
    ) -> BaselineGenerationOutcome:
        request = ProviderRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=serialize_context(context),
            output_schema=OUTPUT_SCHEMA,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
        )
        attempts = []
        final_type = "unknown_failure"
        final_message = "provider did not return a valid baseline answer"
        for attempt_number in range(1, self.max_attempts + 1):
            response = None
            try:
                response = self.provider.generate(request)
                result = _parse_and_validate(response.output_text, context)
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
                return BaselineGenerationOutcome(
                    result=result,
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
                final_type, final_message = type(exc).__name__, str(exc)
                attempts.append(
                    ProviderAttempt(
                        attempt=attempt_number,
                        valid=False,
                        error_type=final_type,
                        error_message=final_message,
                    )
                )
                if not exc.retryable:
                    break
            except OutputValidationError as exc:
                final_type, final_message = type(exc).__name__, str(exc)
                attempts.append(
                    ProviderAttempt(
                        attempt=attempt_number,
                        valid=False,
                        error_type=final_type,
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
        return BaselineGenerationOutcome(
            failure=BaselineFailure(error_type=final_type, message=final_message),
            context=context,
            prompt_version=PROMPT_VERSION,
            prompt_hash=PROMPT_HASH,
            provider=self.provider.provider_name,
            requested_model=self.provider.requested_model,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            attempts=attempts,
        )
