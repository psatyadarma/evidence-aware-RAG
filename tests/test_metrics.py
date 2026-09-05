import pytest

from app.models import AnswerabilityClassification as Label
from app.models import ExpectedAnswer
from evaluation.metrics import (
    abstention_metrics,
    answerability_metrics,
    coverage,
    mean_recall_at_k,
    numeric_answer_correct,
    recall_at_k,
)


def test_answerability_accuracy_per_class_and_macro_f1() -> None:
    expected = [
        Label.ANSWERABLE,
        Label.ANSWERABLE,
        Label.PARTIALLY_ANSWERABLE,
        Label.INSUFFICIENT_EVIDENCE,
        Label.OUT_OF_SCOPE,
    ]
    predicted = [
        Label.ANSWERABLE,
        Label.INSUFFICIENT_EVIDENCE,
        Label.PARTIALLY_ANSWERABLE,
        Label.INSUFFICIENT_EVIDENCE,
        Label.INSUFFICIENT_EVIDENCE,
    ]

    metrics = answerability_metrics(expected, predicted)

    assert metrics.accuracy == pytest.approx(0.6)
    assert metrics.per_class[Label.ANSWERABLE].precision == pytest.approx(1.0)
    assert metrics.per_class[Label.ANSWERABLE].recall == pytest.approx(0.5)
    assert metrics.per_class[Label.INSUFFICIENT_EVIDENCE].precision == pytest.approx(1 / 3)
    assert metrics.per_class[Label.OUT_OF_SCOPE].recall == 0.0
    assert metrics.macro_f1 == pytest.approx((2 / 3 + 1 + 0.5 + 0) / 4)


def test_empty_answerability_inputs_return_defined_zeros() -> None:
    metrics = answerability_metrics([], [])

    assert metrics.count == 0
    assert metrics.accuracy == 0.0
    assert metrics.macro_f1 == 0.0


def test_answerability_length_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="equal length"):
        answerability_metrics([Label.ANSWERABLE], [])


def test_partial_abstention_counts_as_false_positive() -> None:
    expected = [
        Label.ANSWERABLE,
        Label.PARTIALLY_ANSWERABLE,
        Label.INSUFFICIENT_EVIDENCE,
        Label.OUT_OF_SCOPE,
    ]
    predicted_abstained = [False, True, True, False]

    metrics = abstention_metrics(expected, predicted_abstained)

    assert metrics.true_positives == 1
    assert metrics.precision == pytest.approx(0.5)
    assert metrics.recall == pytest.approx(0.5)


def test_no_predicted_abstentions_has_zero_precision() -> None:
    metrics = abstention_metrics(
        [Label.INSUFFICIENT_EVIDENCE, Label.OUT_OF_SCOPE],
        [False, False],
    )

    assert metrics.precision == 0.0
    assert metrics.recall == 0.0


def test_coverage_counts_non_abstained_partial_as_answered() -> None:
    assert coverage([False, False, True, True]) == pytest.approx(0.5)
    assert coverage([]) == 0.0


def test_recall_at_k() -> None:
    required = ["a", "b"]
    retrieved = ["b", "x", "a"]

    assert recall_at_k(required, retrieved, 1) == pytest.approx(0.5)
    assert recall_at_k(required, retrieved, 2) == pytest.approx(0.5)
    assert recall_at_k(required, retrieved, 3) == pytest.approx(1.0)
    assert recall_at_k(required, [], 3) == 0.0
    assert recall_at_k([], retrieved, 3) is None


def test_mean_recall_skips_examples_without_required_evidence() -> None:
    result = mean_recall_at_k(
        [(["a", "b"], ["a"]), ([], ["anything"]), (["c"], [])],
        1,
    )

    assert result == pytest.approx(0.25)
    assert mean_recall_at_k([([], [])], 1) is None


def test_recall_requires_positive_k() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        recall_at_k(["a"], ["a"], 0)


def test_numeric_answer_exact_and_tolerance() -> None:
    exact = ExpectedAnswer(canonical="Ten", numeric_value=10, numeric_tolerance=0)
    tolerant = ExpectedAnswer(canonical="Approximately ten", numeric_value=10, numeric_tolerance=0.1)
    text_only = ExpectedAnswer(canonical="Central Region")

    assert numeric_answer_correct(exact, 10) is True
    assert numeric_answer_correct(exact, 10.01) is False
    assert numeric_answer_correct(tolerant, 10.09) is True
    assert numeric_answer_correct(tolerant, 10.11) is False
    assert numeric_answer_correct(tolerant, None) is False
    assert numeric_answer_correct(text_only, 10) is None

