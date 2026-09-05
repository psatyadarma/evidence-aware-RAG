"""Pre-registered development-only selection of one scalar similarity threshold."""

from __future__ import annotations

import math
from typing import Optional, Sequence

from pydantic import Field

from app.models import AnswerabilityClassification, StrictModel


class BinaryGateLabel(str):
    ALLOW = "ALLOW"
    ABSTAIN = "ABSTAIN"


def binary_gate_label(label: AnswerabilityClassification) -> str:
    if label in {
        AnswerabilityClassification.ANSWERABLE,
        AnswerabilityClassification.PARTIALLY_ANSWERABLE,
    }:
        return BinaryGateLabel.ALLOW
    return BinaryGateLabel.ABSTAIN


class ThresholdObservation(StrictModel):
    id: str = Field(min_length=1)
    label: str = Field(pattern=r"^(ALLOW|ABSTAIN)$")
    score: Optional[float]


class ThresholdCandidateResult(StrictModel):
    threshold: float
    binary_macro_f1: float = Field(ge=0.0, le=1.0)
    abstention_recall: float = Field(ge=0.0, le=1.0)
    predictions: list[str]


class ThresholdSelectionResult(StrictModel):
    selected_threshold: float
    objective: str
    tie_break_order: list[str]
    selected_binary_macro_f1: float
    selected_abstention_recall: float
    candidates: list[ThresholdCandidateResult] = Field(min_length=1)
    observations: list[ThresholdObservation] = Field(min_length=1)
    score_distribution: dict[str, Optional[float]]


def _safe_divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(actual: Sequence[str], predicted: Sequence[str], positive: str) -> float:
    tp = sum(a == positive and p == positive for a, p in zip(actual, predicted))
    fp = sum(a != positive and p == positive for a, p in zip(actual, predicted))
    fn = sum(a == positive and p != positive for a, p in zip(actual, predicted))
    precision = _safe_divide(tp, tp + fp)
    recall = _safe_divide(tp, tp + fn)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def select_threshold(
    observations: Sequence[ThresholdObservation],
) -> ThresholdSelectionResult:
    if not observations:
        raise ValueError("threshold selection requires observations")
    finite_scores = sorted(
        {item.score for item in observations if item.score is not None}
    )
    if not finite_scores or any(not math.isfinite(score) for score in finite_scores):
        raise ValueError("threshold selection requires at least one finite score")
    candidate_values = [math.nextafter(finite_scores[0], -math.inf)]
    candidate_values.extend(finite_scores)
    candidate_values.append(math.nextafter(finite_scores[-1], math.inf))
    actual = [item.label for item in observations]
    candidates = []
    for threshold in candidate_values:
        predictions = [
            BinaryGateLabel.ALLOW
            if item.score is not None and item.score >= threshold
            else BinaryGateLabel.ABSTAIN
            for item in observations
        ]
        macro_f1 = (
            _f1(actual, predictions, BinaryGateLabel.ALLOW)
            + _f1(actual, predictions, BinaryGateLabel.ABSTAIN)
        ) / 2
        abstain_total = sum(item == BinaryGateLabel.ABSTAIN for item in actual)
        abstain_correct = sum(
            a == BinaryGateLabel.ABSTAIN and p == BinaryGateLabel.ABSTAIN
            for a, p in zip(actual, predictions)
        )
        candidates.append(
            ThresholdCandidateResult(
                threshold=threshold,
                binary_macro_f1=macro_f1,
                abstention_recall=_safe_divide(abstain_correct, abstain_total),
                predictions=predictions,
            )
        )
    indexed = list(enumerate(candidates))
    _, selected = max(
        indexed,
        key=lambda item: (
            item[1].binary_macro_f1,
            item[1].abstention_recall,
            item[1].threshold,
            -item[0],
        ),
    )
    allow_scores = [
        item.score
        for item in observations
        if item.score is not None and item.label == BinaryGateLabel.ALLOW
    ]
    abstain_scores = [
        item.score
        for item in observations
        if item.score is not None and item.label == BinaryGateLabel.ABSTAIN
    ]
    return ThresholdSelectionResult(
        selected_threshold=selected.threshold,
        objective="maximize binary macro F1 for ALLOW versus ABSTAIN",
        tie_break_order=[
            "higher abstention recall",
            "higher threshold",
            "earlier deterministic numeric candidate order",
        ],
        selected_binary_macro_f1=selected.binary_macro_f1,
        selected_abstention_recall=selected.abstention_recall,
        candidates=candidates,
        observations=list(observations),
        score_distribution={
            "overall_min": min(finite_scores),
            "overall_max": max(finite_scores),
            "allow_min": min(allow_scores) if allow_scores else None,
            "allow_max": max(allow_scores) if allow_scores else None,
            "abstain_min": min(abstain_scores) if abstain_scores else None,
            "abstain_max": max(abstain_scores) if abstain_scores else None,
            "none_count": float(sum(item.score is None for item in observations)),
        },
    )
