"""Frozen-gate orchestration for deterministic abstention or grounded generation."""

from __future__ import annotations

from typing import Iterable, Optional

from pydantic import Field, model_validator

from app.answerability import (
    DecisionReason,
    DeterministicAnswerabilityClassifier,
    DeterministicAnswerabilityDecision,
)
from app.computation import DeterministicComputationEngine, VerifiedComputation
from app.generation import (
    GroundedAnswerGenerator,
    GroundedGeneratedAnswer,
    GroundedGenerationOutcome,
    build_generation_context,
)
from app.models import AnswerabilityClassification, RetrievalUnit, StrictModel


FULL_ABSTENTION_CLASSES = {
    AnswerabilityClassification.INSUFFICIENT_EVIDENCE,
    AnswerabilityClassification.OUT_OF_SCOPE,
}


class PipelineResponse(StrictModel):
    classification: AnswerabilityClassification
    answer: Optional[str]
    abstained: bool
    generation_called: bool
    decision: DeterministicAnswerabilityDecision
    computation: Optional[VerifiedComputation]
    generated: Optional[GroundedGeneratedAnswer]
    generation_outcome: Optional[GroundedGenerationOutcome]
    failure: Optional[str]

    @model_validator(mode="after")
    def route_is_consistent(self) -> "PipelineResponse":
        if self.abstained:
            if self.generation_called or self.computation is not None or self.generated is not None:
                raise ValueError("deterministic abstention cannot contain generation artifacts")
            if self.answer is None:
                raise ValueError("deterministic abstention requires a user-facing response")
        elif not self.generation_called or self.computation is None:
            raise ValueError("substantive route requires computation and a generator call")
        if self.generated is not None and self.answer != self.generated.answer:
            raise ValueError("pipeline answer must match the validated generated answer")
        if self.failure is not None and self.answer is not None:
            raise ValueError("generation failure cannot fabricate a fallback answer")
        return self


_ABSTENTION_MESSAGES = {
    DecisionReason.UNSUPPORTED_ATTRIBUTE: "That attribute is outside this population corpus.",
    DecisionReason.REQUESTED_YEAR_UNAVAILABLE: "The requested year is not available at this geography and measure.",
    DecisionReason.REQUESTED_GEOGRAPHY_UNAVAILABLE: "The requested geography is not available at the required granularity.",
    DecisionReason.VALUE_NOT_AVAILABLE: "The official source marks the requested value as not available; it is not zero.",
    DecisionReason.CAUSAL_EVIDENCE_MISSING: "The corpus contains descriptive observations but no evidence establishing the requested cause.",
    DecisionReason.PREDICTION_REQUESTED: "The historical corpus does not provide evidence for a forecast.",
    DecisionReason.FALSE_PREMISE: "The supplied observations contradict the question's premise, and the corpus does not establish a cause.",
    DecisionReason.AMBIGUOUS_GEOGRAPHY: "The place name does not resolve to one unambiguous geography level in the corpus.",
    DecisionReason.BOUNDARY_DEFINITION_CHANGE: "The observations cross incompatible planning-boundary definitions.",
    DecisionReason.MISSING_REQUIRED_OPERANDS: "One or more observations required to answer are missing.",
    DecisionReason.INCOMPATIBLE_COMPARISON: "The available observations are not directly comparable.",
    DecisionReason.QUERY_NOT_RESOLVED: "The requested population measure or operation could not be resolved from this corpus.",
}


def deterministic_abstention(decision: DeterministicAnswerabilityDecision) -> str:
    reasons = [reason for reason in decision.reasons if reason in _ABSTENTION_MESSAGES]
    if DecisionReason.FALSE_PREMISE in reasons:
        reasons = [DecisionReason.FALSE_PREMISE]
    elif DecisionReason.UNSUPPORTED_ATTRIBUTE in reasons:
        reasons = [DecisionReason.UNSUPPORTED_ATTRIBUTE]
    elif DecisionReason.VALUE_NOT_AVAILABLE in reasons:
        reasons = [DecisionReason.VALUE_NOT_AVAILABLE]
    messages = list(dict.fromkeys(_ABSTENTION_MESSAGES[reason] for reason in reasons))
    if not messages:
        messages = ["The available evidence is insufficient to answer this question."]
    return " ".join(messages)


class EvidenceAwarePipeline:
    """Enforce that the LLM never runs before or in place of the frozen gate."""

    def __init__(
        self,
        classifier: DeterministicAnswerabilityClassifier,
        computation_engine: DeterministicComputationEngine,
        generator: GroundedAnswerGenerator,
        corpus_units: Iterable[RetrievalUnit],
    ) -> None:
        self.classifier = classifier
        self.computation_engine = computation_engine
        self.generator = generator
        self.corpus_units = tuple(corpus_units)

    def answer(self, question: str) -> PipelineResponse:
        decision = self.classifier.decide(question)
        if decision.classification in FULL_ABSTENTION_CLASSES:
            return PipelineResponse(
                classification=decision.classification,
                answer=deterministic_abstention(decision),
                abstained=True,
                generation_called=False,
                decision=decision,
                computation=None,
                generated=None,
                generation_outcome=None,
                failure=None,
            )
        computation = self.computation_engine.compute(decision)
        context = build_generation_context(
            question, decision, computation, self.corpus_units
        )
        outcome = self.generator.generate(context)
        if outcome.answer is None:
            assert outcome.failure is not None
            return PipelineResponse(
                classification=decision.classification,
                answer=None,
                abstained=False,
                generation_called=True,
                decision=decision,
                computation=computation,
                generated=None,
                generation_outcome=outcome,
                failure=f"{outcome.failure.error_type}: {outcome.failure.message}",
            )
        return PipelineResponse(
            classification=decision.classification,
            answer=outcome.answer.answer,
            abstained=False,
            generation_called=True,
            decision=decision,
            computation=computation,
            generated=outcome.answer,
            generation_outcome=outcome,
            failure=None,
        )
