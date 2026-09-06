"""Presentation controller for Streamlit and CLI over the frozen pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from app.models import AnswerabilityClassification, RetrievalUnit
from app.runtime import RuntimeBundle


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvidenceView:
    evidence_id: str
    dataset: str
    publisher: str
    source_url: str
    geography: str
    year: int
    measure: str
    demographics: dict[str, str]
    value: Optional[Union[float, int]]
    raw_value: Optional[str]
    status: str


@dataclass(frozen=True)
class ResultView:
    status: str
    answer: Optional[str]
    message: str
    generation_invoked: bool
    evidence: list[EvidenceView] = field(default_factory=list)
    qualifications: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    refusal_reasons: list[str] = field(default_factory=list)
    transparency: dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None


def _evidence_view(unit: RetrievalUnit) -> EvidenceView:
    dimensions = unit.dimensions
    geography = (
        dimensions.get("subzone")
        or dimensions.get("planning_area")
        or dimensions.get("planning_region")
        or dimensions.get("geography")
        or "Singapore"
    )
    measure = dimensions.get("series") or "Resident population"
    demographic_keys = {
        "age_group",
        "sex",
        "planning_area",
        "geography_level",
    }
    demographics = {
        key.replace("_", " ").title(): value
        for key, value in dimensions.items()
        if key in demographic_keys and value not in {"Total", geography}
    }
    return EvidenceView(
        evidence_id=unit.evidence_id,
        dataset=unit.metadata.dataset_name,
        publisher=unit.metadata.publisher,
        source_url=str(unit.metadata.source_url),
        geography=geography,
        year=unit.year,
        measure=measure,
        demographics=demographics,
        value=unit.value,
        raw_value=unit.raw_value,
        status=unit.status.value,
    )


def _transparency(response: Any) -> dict[str, Any]:
    query = response.decision.structured_query
    geographies = [item.entity for item in query.geographies]
    if not geographies and query.geography_level is not None:
        geographies = ["Singapore"] if query.geography_level.value == "national" else []
    age = query.age_constraint.model_dump(mode="json") if query.age_constraint else None
    return {
        "interpreted_measure": query.requested_measure or query.unsupported_attribute or "Unresolved",
        "years": query.years,
        "geography_level": query.geography_level.value if query.geography_level else None,
        "geographies": geographies,
        "age_filter": age,
        "resolved_age_groups": query.resolved_age_groups,
        "sexes": query.sexes,
        "requested_operation": query.operation.value,
        "evidence_operation": query.evidence_operation.value,
        "answerability_decision": response.classification.value,
        "generation_invoked": response.generation_called,
    }


def _failure_view(response: Any) -> ResultView:
    outcome = response.generation_outcome
    error_type = outcome.failure.error_type if outcome and outcome.failure else "ProviderError"
    if error_type == "MissingApiKeyError":
        code = "MISSING_API_KEY"
        message = (
            "This question passed the evidence checks and requires grounded generation, "
            "but RAG_MODEL_API_KEY is not configured. Deterministic abstentions remain available."
        )
    elif error_type == "OutputValidationError":
        code = "MALFORMED_MODEL_RESPONSE"
        message = "The model response could not be validated. No answer is being shown."
    else:
        code = "PROVIDER_ERROR"
        message = "The generation provider is temporarily unavailable. No answer is being shown."
    LOGGER.warning("request_failed error_code=%s error_type=%s", code, error_type)
    return ResultView(
        status="ERROR",
        answer=None,
        message=message,
        generation_invoked=True,
        transparency=_transparency(response),
        error_code=code,
    )


class ApplicationController:
    """Convert structured pipeline output into safe, user-facing view data."""

    def __init__(self, runtime: RuntimeBundle) -> None:
        self.runtime = runtime
        self._by_id = {unit.evidence_id: unit for unit in runtime.units}

    def answer(self, question: str) -> ResultView:
        cleaned = question.strip()
        if not cleaned:
            return ResultView(
                status="INPUT_ERROR",
                answer=None,
                message="Enter a population or demographic question.",
                generation_invoked=False,
                error_code="EMPTY_QUESTION",
            )
        try:
            response = self.runtime.pipeline.answer(cleaned)
        except Exception as exc:  # pragma: no cover - defensive application boundary
            LOGGER.exception("unexpected_request_failure error_type=%s", type(exc).__name__)
            return ResultView(
                status="ERROR",
                answer=None,
                message="An unexpected internal error occurred. No answer is being shown.",
                generation_invoked=False,
                error_code="INTERNAL_ERROR",
            )
        if response.failure is not None:
            return _failure_view(response)

        if response.computation is not None:
            evidence_ids = response.computation.facts[0].evidence_ids
            qualifications = [item.text for item in response.computation.qualifications]
        else:
            evidence_ids = response.decision.retrieved_evidence_ids
            qualifications = []
        evidence = [
            _evidence_view(self._by_id[evidence_id])
            for evidence_id in evidence_ids
            if evidence_id in self._by_id
        ]
        limitations = []
        if response.generation_outcome is not None:
            limitations = [
                item.explanation
                for item in response.generation_outcome.context.unsupported_components
            ]
        refusal_reasons = [reason.value.replace("_", " ").title() for reason in response.decision.reasons]
        message = response.answer or "No answer was produced."
        LOGGER.info(
            "request_completed status=%s generation_invoked=%s evidence_count=%d",
            response.classification.value,
            response.generation_called,
            len(evidence),
        )
        return ResultView(
            status=response.classification.value,
            answer=response.answer if not response.abstained else None,
            message=message,
            generation_invoked=response.generation_called,
            evidence=evidence,
            qualifications=qualifications,
            limitations=limitations,
            refusal_reasons=refusal_reasons if response.abstained else [],
            transparency=_transparency(response),
        )


def status_is_answer(result: ResultView) -> bool:
    return result.status in {
        AnswerabilityClassification.ANSWERABLE.value,
        AnswerabilityClassification.PARTIALLY_ANSWERABLE.value,
    } and result.error_code is None
