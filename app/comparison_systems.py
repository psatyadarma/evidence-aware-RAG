"""Complete answering policies for Systems A and B; System C stays frozen."""

from __future__ import annotations

from time import perf_counter
from typing import Optional

from pydantic import Field, model_validator

from app.baseline_generation import (
    BaselineAction,
    BaselineAnswerGenerator,
    BaselineGenerationOutcome,
    build_baseline_context,
)
from app.models import StrictModel
from app.structured_retrieval import StructuredRetriever
from app.threshold_gate import (
    SimilarityThresholdGate,
    StructuredEvidenceSimilarityScorer,
    ThresholdScore,
)


THRESHOLD_ABSTENTION_RESPONSE = (
    "The maximum structured-evidence similarity did not meet the frozen threshold, "
    "so this baseline abstains."
)


class BaselinePolicyResponse(StrictModel):
    system_id: str = Field(pattern=r"^system_[ab]$")
    action: BaselineAction
    answer: Optional[str]
    limitations: list[str]
    claims: list[dict]
    cited_evidence_ids: list[str]
    structured_evidence_ids: list[str]
    threshold_score: Optional[ThresholdScore]
    threshold: Optional[float]
    threshold_abstained: bool
    model_abstained: bool
    generation_called: bool
    generation_outcome: Optional[BaselineGenerationOutcome]
    failure: Optional[str]
    total_latency_ms: float = Field(ge=0.0)

    @model_validator(mode="after")
    def policy_state_is_consistent(self) -> "BaselinePolicyResponse":
        if self.threshold_abstained and self.generation_called:
            raise ValueError("threshold abstention cannot call generation")
        if self.model_abstained and not self.generation_called:
            raise ValueError("model abstention requires a generation call")
        if self.failure is not None and self.action != BaselineAction.ABSTAIN:
            raise ValueError("provider failure cannot be an answer")
        return self


def _from_generation(
    *,
    system_id: str,
    evidence_ids: list[str],
    outcome: BaselineGenerationOutcome,
    threshold_score: Optional[ThresholdScore],
    threshold: Optional[float],
    started: float,
) -> BaselinePolicyResponse:
    if outcome.result is None:
        assert outcome.failure is not None
        return BaselinePolicyResponse(
            system_id=system_id,
            action=BaselineAction.ABSTAIN,
            answer=None,
            limitations=[],
            claims=[],
            cited_evidence_ids=[],
            structured_evidence_ids=evidence_ids,
            threshold_score=threshold_score,
            threshold=threshold,
            threshold_abstained=False,
            model_abstained=False,
            generation_called=True,
            generation_outcome=outcome,
            failure=f"{outcome.failure.error_type}: {outcome.failure.message}",
            total_latency_ms=(perf_counter() - started) * 1_000,
        )
    result = outcome.result
    cited = list(
        dict.fromkeys(
            evidence_id for claim in result.claims for evidence_id in claim.evidence_ids
        )
    )
    return BaselinePolicyResponse(
        system_id=system_id,
        action=result.action,
        answer=result.answer,
        limitations=result.limitations,
        claims=[item.model_dump(mode="json") for item in result.claims],
        cited_evidence_ids=cited,
        structured_evidence_ids=evidence_ids,
        threshold_score=threshold_score,
        threshold=threshold,
        threshold_abstained=False,
        model_abstained=result.action == BaselineAction.ABSTAIN,
        generation_called=True,
        generation_outcome=outcome,
        failure=None,
        total_latency_ms=(perf_counter() - started) * 1_000,
    )


class UngatedRagSystem:
    system_id = "system_a"

    def __init__(
        self, retriever: StructuredRetriever, generator: BaselineAnswerGenerator
    ) -> None:
        self.retriever = retriever
        self.generator = generator

    def answer(self, question: str) -> BaselinePolicyResponse:
        started = perf_counter()
        results = self.retriever.plan(question).results
        evidence_ids = [item.evidence.evidence_id for item in results]
        outcome = self.generator.generate(build_baseline_context(question, results))
        return _from_generation(
            system_id=self.system_id,
            evidence_ids=evidence_ids,
            outcome=outcome,
            threshold_score=None,
            threshold=None,
            started=started,
        )


class ThresholdRagSystem:
    system_id = "system_b"

    def __init__(
        self,
        retriever: StructuredRetriever,
        scorer: StructuredEvidenceSimilarityScorer,
        generator: BaselineAnswerGenerator,
        threshold: float,
    ) -> None:
        self.retriever = retriever
        self.scorer = scorer
        self.generator = generator
        self.gate = SimilarityThresholdGate(threshold)

    def answer(self, question: str) -> BaselinePolicyResponse:
        started = perf_counter()
        results = self.retriever.plan(question).results
        evidence_ids = [item.evidence.evidence_id for item in results]
        score = self.scorer.score(question, results)
        if not self.gate.allows(score):
            return BaselinePolicyResponse(
                system_id=self.system_id,
                action=BaselineAction.ABSTAIN,
                answer=None,
                limitations=[THRESHOLD_ABSTENTION_RESPONSE],
                claims=[],
                cited_evidence_ids=[],
                structured_evidence_ids=evidence_ids,
                threshold_score=score,
                threshold=self.gate.threshold,
                threshold_abstained=True,
                model_abstained=False,
                generation_called=False,
                generation_outcome=None,
                failure=None,
                total_latency_ms=(perf_counter() - started) * 1_000,
            )
        outcome = self.generator.generate(build_baseline_context(question, results))
        return _from_generation(
            system_id=self.system_id,
            evidence_ids=evidence_ids,
            outcome=outcome,
            threshold_score=score,
            threshold=self.gate.threshold,
            started=started,
        )
