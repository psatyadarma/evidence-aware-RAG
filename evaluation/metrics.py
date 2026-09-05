"""Deterministic metrics for future system predictions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from app.models import AnswerabilityClassification, ExpectedAnswer


FULL_ABSTENTION_CLASSES = frozenset(
    {
        AnswerabilityClassification.INSUFFICIENT_EVIDENCE,
        AnswerabilityClassification.OUT_OF_SCOPE,
    }
)


@dataclass(frozen=True)
class PerClassMetrics:
    precision: float
    recall: float
    f1: float
    support: int


@dataclass(frozen=True)
class ClassificationMetrics:
    accuracy: float
    per_class: dict[AnswerabilityClassification, PerClassMetrics]
    macro_f1: float
    count: int


@dataclass(frozen=True)
class AbstentionMetrics:
    precision: float
    recall: float
    true_positives: int
    predicted_abstentions: int
    required_abstentions: int


def _safe_divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def answerability_metrics(
    expected: Sequence[AnswerabilityClassification],
    predicted: Sequence[AnswerabilityClassification],
) -> ClassificationMetrics:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted labels must have equal length")
    count = len(expected)
    accuracy = _safe_divide(
        sum(gold == prediction for gold, prediction in zip(expected, predicted)),
        count,
    )
    per_class: dict[AnswerabilityClassification, PerClassMetrics] = {}
    for label in AnswerabilityClassification:
        true_positives = sum(
            gold == label and prediction == label
            for gold, prediction in zip(expected, predicted)
        )
        false_positives = sum(
            gold != label and prediction == label
            for gold, prediction in zip(expected, predicted)
        )
        false_negatives = sum(
            gold == label and prediction != label
            for gold, prediction in zip(expected, predicted)
        )
        support = sum(gold == label for gold in expected)
        precision = _safe_divide(true_positives, true_positives + false_positives)
        recall = _safe_divide(true_positives, true_positives + false_negatives)
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        per_class[label] = PerClassMetrics(
            precision=precision,
            recall=recall,
            f1=f1,
            support=support,
        )
    macro_f1 = sum(metric.f1 for metric in per_class.values()) / len(per_class)
    return ClassificationMetrics(
        accuracy=accuracy,
        per_class=per_class,
        macro_f1=macro_f1,
        count=count,
    )


def abstention_metrics(
    expected: Sequence[AnswerabilityClassification],
    predicted_abstained: Sequence[bool],
) -> AbstentionMetrics:
    if len(expected) != len(predicted_abstained):
        raise ValueError("expected labels and abstention decisions must have equal length")
    requires_abstention = [label in FULL_ABSTENTION_CLASSES for label in expected]
    true_positives = sum(
        required and abstained
        for required, abstained in zip(requires_abstention, predicted_abstained)
    )
    predicted_count = sum(predicted_abstained)
    required_count = sum(requires_abstention)
    return AbstentionMetrics(
        precision=_safe_divide(true_positives, predicted_count),
        recall=_safe_divide(true_positives, required_count),
        true_positives=true_positives,
        predicted_abstentions=predicted_count,
        required_abstentions=required_count,
    )


def coverage(predicted_abstained: Sequence[bool]) -> float:
    """Fraction receiving a substantive answer; non-abstained partials count as covered."""

    return _safe_divide(sum(not value for value in predicted_abstained), len(predicted_abstained))


def recall_at_k(
    required_evidence_ids: Sequence[str],
    retrieved_evidence_ids: Sequence[str],
    k: int,
) -> Optional[float]:
    if k < 1:
        raise ValueError("k must be at least 1")
    required = set(required_evidence_ids)
    if not required:
        return None
    retrieved = set(retrieved_evidence_ids[:k])
    return len(required & retrieved) / len(required)


def mean_recall_at_k(
    examples: Iterable[tuple[Sequence[str], Sequence[str]]], k: int
) -> Optional[float]:
    values = [
        value
        for required, retrieved in examples
        if (value := recall_at_k(required, retrieved, k)) is not None
    ]
    return sum(values) / len(values) if values else None


def numeric_answer_correct(
    expected: ExpectedAnswer, predicted_value: Optional[float]
) -> Optional[bool]:
    """Return None when no numeric ground truth exists; free text needs later review."""

    if expected.numeric_value is None:
        return None
    if predicted_value is None or not math.isfinite(predicted_value):
        return False
    tolerance = expected.numeric_tolerance or 0.0
    return math.isclose(
        float(predicted_value),
        float(expected.numeric_value),
        rel_tol=0.0,
        abs_tol=tolerance,
    )
