"""Development-set comparison of deterministic and LLM sufficiency decisions."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Iterable, Optional, Sequence

from app.answerability import DeterministicAnswerabilityClassifier
from app.llm_answerability import (
    LlmClassificationOutcome,
    LlmEvidenceSufficiencyClassifier,
)
from app.models import (
    AnswerabilityClassification,
    BenchmarkCategory,
    BenchmarkExample,
)
from evaluation.metrics import FULL_ABSTENTION_CLASSES


STANDARD_INPUT_USD_PER_MILLION = 0.40
CACHED_INPUT_USD_PER_MILLION = 0.10
OUTPUT_USD_PER_MILLION = 1.60
PRICING_AS_OF = "2026-09-05"
PRICING_URL = "https://developers.openai.com/api/docs/models/gpt-4.1-mini"


def _safe_divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _metrics_with_failures(
    expected: Sequence[AnswerabilityClassification],
    predicted: Sequence[Optional[AnswerabilityClassification]],
) -> dict:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted labels must have equal length")
    per_class: dict[str, dict] = {}
    f1_values: list[float] = []
    for label in AnswerabilityClassification:
        true_positives = sum(
            gold == label and guess == label
            for gold, guess in zip(expected, predicted)
        )
        false_positives = sum(
            guess == label and gold != label
            for gold, guess in zip(expected, predicted)
        )
        false_negatives = sum(
            gold == label and guess != label
            for gold, guess in zip(expected, predicted)
        )
        precision = _safe_divide(true_positives, true_positives + false_positives)
        recall = _safe_divide(true_positives, true_positives + false_negatives)
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        f1_values.append(f1)
        per_class[label.value] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": sum(gold == label for gold in expected),
        }
    return {
        "accuracy": _safe_divide(
            sum(gold == guess for gold, guess in zip(expected, predicted)),
            len(expected),
        ),
        "macro_f1": sum(f1_values) / len(f1_values),
        "per_class": per_class,
        "successful_prediction_count": sum(guess is not None for guess in predicted),
        "failure_count": sum(guess is None for guess in predicted),
    }


def _confusion_matrix(
    expected: Sequence[AnswerabilityClassification],
    predicted: Sequence[Optional[AnswerabilityClassification]],
) -> dict[str, dict[str, int]]:
    columns = [label.value for label in AnswerabilityClassification] + ["MODEL_FAILURE"]
    return {
        actual.value: {
            column: sum(
                gold == actual
                and (
                    (guess.value if guess is not None else "MODEL_FAILURE") == column
                )
                for gold, guess in zip(expected, predicted)
            )
            for column in columns
        }
        for actual in AnswerabilityClassification
    }


def _abstention_metrics(
    expected: Sequence[AnswerabilityClassification],
    predicted: Sequence[Optional[AnswerabilityClassification]],
) -> dict:
    required = [label in FULL_ABSTENTION_CLASSES for label in expected]
    predicted_abstention = [
        label in FULL_ABSTENTION_CLASSES if label is not None else False
        for label in predicted
    ]
    true_positives = sum(
        needed and abstained
        for needed, abstained in zip(required, predicted_abstention)
    )
    return {
        "precision": _safe_divide(true_positives, sum(predicted_abstention)),
        "recall": _safe_divide(true_positives, sum(required)),
        "true_positives": true_positives,
        "predicted_abstentions": sum(predicted_abstention),
        "required_abstentions": sum(required),
        "model_failures_are_not_counted_as_abstentions": True,
    }


def _error_category(
    example: BenchmarkExample,
    predicted: Optional[AnswerabilityClassification],
    outcome: LlmClassificationOutcome,
) -> Optional[str]:
    if predicted == example.answerability:
        return None
    if outcome.failure is not None:
        if outcome.failure.error_type == "OutputValidationError":
            if "not supplied" in outcome.failure.message:
                return "evidence_citation_mismatch"
            return "structured_output_failure"
        return "provider_failure"
    if example.category == BenchmarkCategory.UNSUPPORTED_CAUSALITY:
        return "accepted_unsupported_causality"
    if example.category == BenchmarkCategory.FALSE_PREMISE:
        return "accepted_or_mishandled_false_premise"
    if example.category == BenchmarkCategory.PARTIALLY_ANSWERABLE:
        return "failed_partial_decomposition"
    if example.category == BenchmarkCategory.TEMPORAL_MISMATCH:
        return "ignored_missing_year"
    if example.category == BenchmarkCategory.GEOGRAPHIC_MISMATCH:
        return "ignored_missing_or_incompatible_geography"
    if example.category == BenchmarkCategory.UNAVAILABLE_VALUE:
        return "ignored_not_available_or_ambiguity"
    if example.answerability == AnswerabilityClassification.OUT_OF_SCOPE:
        return "misclassified_out_of_scope"
    if predicted == AnswerabilityClassification.OUT_OF_SCOPE:
        return "confused_relevance_with_sufficiency"
    return "evidence_sufficiency_policy_error"


def _comparison_outcome(
    gold: AnswerabilityClassification,
    deterministic: AnswerabilityClassification,
    llm: Optional[AnswerabilityClassification],
) -> str:
    deterministic_correct = deterministic == gold
    llm_correct = llm == gold
    if deterministic_correct and llm_correct:
        return "both_correct"
    if deterministic_correct:
        return "deterministic_only_correct"
    if llm_correct:
        return "llm_only_correct"
    return "both_incorrect"


def evaluate_llm_answerability(
    examples: Sequence[BenchmarkExample],
    deterministic_classifier: DeterministicAnswerabilityClassifier,
    llm_classifier: LlmEvidenceSufficiencyClassifier,
) -> tuple[list[dict], dict]:
    report: list[dict] = []
    expected: list[AnswerabilityClassification] = []
    deterministic_predictions: list[AnswerabilityClassification] = []
    llm_predictions: list[Optional[AnswerabilityClassification]] = []
    all_attempts = []
    actual_models: set[str] = set()

    for example in examples:
        deterministic = deterministic_classifier.decide(example.question)
        outcome = llm_classifier.classify(example.question)
        llm_prediction = (
            outcome.decision.classification if outcome.decision is not None else None
        )
        expected.append(example.answerability)
        deterministic_predictions.append(deterministic.classification)
        llm_predictions.append(llm_prediction)
        all_attempts.extend(outcome.attempts)
        actual_models.update(
            attempt.model for attempt in outcome.attempts if attempt.model is not None
        )
        error_category = _error_category(example, llm_prediction, outcome)
        decision = outcome.decision
        report.append(
            {
                "id": example.id,
                "question": example.question,
                "ground_truth_label": example.answerability.value,
                "deterministic_prediction": deterministic.classification.value,
                "llm_prediction": (
                    llm_prediction.value if llm_prediction is not None else None
                ),
                "deterministic_llm_agreement": (
                    llm_prediction == deterministic.classification
                    if llm_prediction is not None
                    else False
                ),
                "comparison_outcome": _comparison_outcome(
                    example.answerability,
                    deterministic.classification,
                    llm_prediction,
                ),
                "llm_correct": llm_prediction == example.answerability,
                "llm_cited_evidence_ids": (
                    decision.decision_evidence_ids if decision is not None else []
                ),
                "llm_claims": (
                    [claim.model_dump(mode="json") for claim in decision.claims]
                    if decision is not None
                    else []
                ),
                "llm_reason_codes": (
                    [code.value for code in decision.reason_codes]
                    if decision is not None
                    else []
                ),
                "llm_reason": decision.reason if decision is not None else None,
                "error_category": error_category,
                "model_failure": (
                    outcome.failure.model_dump(mode="json")
                    if outcome.failure is not None
                    else None
                ),
                "provided_evidence_ids": [
                    item.evidence_id for item in outcome.context.evidence
                ],
                "structured_interpretation": outcome.context.structured_interpretation.model_dump(
                    mode="json"
                ),
                "retrieval_diagnostics": outcome.context.retrieval_diagnostics,
                "provider_attempts": [
                    attempt.model_dump(mode="json") for attempt in outcome.attempts
                ],
            }
        )

    llm_metrics = _metrics_with_failures(expected, llm_predictions)
    deterministic_metrics = _metrics_with_failures(
        expected, list(deterministic_predictions)
    )
    latency_values = [
        attempt.latency_ms for attempt in all_attempts if attempt.latency_ms > 0
    ]
    input_tokens = sum(attempt.input_tokens for attempt in all_attempts)
    cached_input_tokens = sum(attempt.cached_input_tokens for attempt in all_attempts)
    output_tokens = sum(attempt.output_tokens for attempt in all_attempts)
    estimated_cost = (
        (input_tokens - cached_input_tokens) * STANDARD_INPUT_USD_PER_MILLION
        + cached_input_tokens * CACHED_INPUT_USD_PER_MILLION
        + output_tokens * OUTPUT_USD_PER_MILLION
    ) / 1_000_000
    summary = {
        "evaluation_split": "development",
        "result_status": (
            "complete" if llm_metrics["failure_count"] == 0 else "completed_with_failures"
        ),
        "example_count": len(examples),
        "provider": llm_classifier.provider.provider_name,
        "requested_model": llm_classifier.provider.requested_model,
        "actual_models": sorted(actual_models),
        "prompt_version": outcome.prompt_version if report else None,
        "prompt_hash": outcome.prompt_hash if report else None,
        "generation_parameters": {
            "temperature": llm_classifier.temperature,
            "max_output_tokens": llm_classifier.max_output_tokens,
            "max_attempts": llm_classifier.max_attempts,
        },
        "deterministic": deterministic_metrics,
        "llm": {
            **llm_metrics,
            "confusion_matrix": _confusion_matrix(expected, llm_predictions),
            "abstention": _abstention_metrics(expected, llm_predictions),
        },
        "comparison": {
            category: sum(row["comparison_outcome"] == category for row in report)
            for category in (
                "both_correct",
                "deterministic_only_correct",
                "llm_only_correct",
                "both_incorrect",
            )
        },
        "errors": [
            {
                "id": row["id"],
                "ground_truth_label": row["ground_truth_label"],
                "llm_prediction": row["llm_prediction"],
                "error_category": row["error_category"],
                "llm_reason_codes": row["llm_reason_codes"],
                "llm_cited_evidence_ids": row["llm_cited_evidence_ids"],
                "model_failure": row["model_failure"],
            }
            for row in report
            if not row["llm_correct"]
        ],
        "cost_and_latency": {
            "total_provider_calls": len(all_attempts),
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": estimated_cost,
            "pricing_as_of": PRICING_AS_OF,
            "pricing_url": PRICING_URL,
            "mean_latency_ms": (
                statistics.mean(latency_values) if latency_values else None
            ),
            "median_latency_ms": (
                statistics.median(latency_values) if latency_values else None
            ),
            "latency_sample_count": len(latency_values),
        },
    }
    return report, summary


def write_llm_answerability_report(
    report: Iterable[dict],
    summary: dict,
    report_path: Path,
    summary_path: Path,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_temp = report_path.with_suffix(report_path.suffix + ".tmp")
    summary_temp = summary_path.with_suffix(summary_path.suffix + ".tmp")
    try:
        with report_temp.open("w", encoding="utf-8", newline="\n") as output:
            for row in report:
                output.write(
                    json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                )
        summary_temp.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report_temp.replace(report_path)
        summary_temp.replace(summary_path)
    except Exception:
        report_temp.unlink(missing_ok=True)
        summary_temp.unlink(missing_ok=True)
        raise
