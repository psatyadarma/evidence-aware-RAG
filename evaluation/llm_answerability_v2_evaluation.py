"""Development-only evaluation for the final Checkpoint 9 v2 contract."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Iterable, Optional, Sequence

from app.answerability import DeterministicAnswerabilityClassifier
from app.llm_answerability_v2 import (
    EvidenceRelation,
    LlmClassificationOutcomeV2,
    LlmEvidenceSufficiencyClassifierV2,
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
                and (guess.value if guess is not None else "MODEL_FAILURE") == column
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


def _crosses_region_boundary(outcome: LlmClassificationOutcomeV2) -> bool:
    sources = {item.source_id for item in outcome.context.evidence}
    years = {item.year for item in outcome.context.evidence}
    return (
        "residents_by_planning_region_annual" in sources
        and 2019 in years
        and any(year >= 2020 for year in years)
    )


def _failure_detail(outcome: LlmClassificationOutcomeV2) -> Optional[str]:
    if outcome.failure is None:
        return None
    message = outcome.failure.message
    relation_markers = (
        "SUPPORTS evidence",
        "may use SUPPORTS",
        "establishing unavailability",
        "ESTABLISHES_UNAVAILABILITY",
        "CONTRADICTS evidence",
        "evidence IDs not supplied",
    )
    if any(marker in message for marker in relation_markers):
        return "evidence_relation_failure"
    return "structured_output_failure"


def _error_category(
    example: BenchmarkExample,
    predicted: Optional[AnswerabilityClassification],
    outcome: LlmClassificationOutcomeV2,
) -> Optional[str]:
    if predicted == example.answerability:
        return None
    failure_detail = _failure_detail(outcome)
    if failure_detail is not None:
        return failure_detail
    if predicted == AnswerabilityClassification.OUT_OF_SCOPE:
        return "out_of_scope_failure"
    if example.category == BenchmarkCategory.UNSUPPORTED_CAUSALITY:
        assert outcome.decision is not None
        supports = [
            claim.support.value
            for claim in outcome.decision.assessment.requested_claims
        ]
        if "SUPPORTED" in supports and "UNSUPPORTED" in supports:
            return "claim_decomposition_failure"
        return "unsupported_causality_acceptance"
    if example.category == BenchmarkCategory.FALSE_PREMISE:
        return "false_premise_failure"
    if example.answerability == AnswerabilityClassification.PARTIALLY_ANSWERABLE:
        if _crosses_region_boundary(outcome):
            return "source_caveat_failure"
        assert outcome.decision is not None
        if len(outcome.decision.assessment.requested_claims) < 2:
            return "claim_decomposition_failure"
        return "partial_answerability_failure"
    if example.answerability == AnswerabilityClassification.OUT_OF_SCOPE:
        return "out_of_scope_failure"
    if example.category == BenchmarkCategory.UNAVAILABLE_VALUE:
        return "evidence_relation_failure"
    return "claim_decomposition_failure"


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


def _all_cited_ids(outcome: LlmClassificationOutcomeV2) -> list[str]:
    if outcome.decision is None:
        return []
    assessment = outcome.decision.assessment
    values = [
        item.evidence_id
        for claim in assessment.requested_claims
        for item in claim.evidence
    ]
    values.extend(
        item.evidence_id
        for premise in assessment.premises
        for item in premise.evidence
    )
    return list(dict.fromkeys(values))


def evaluate_llm_answerability_v2(
    examples: Sequence[BenchmarkExample],
    deterministic_classifier: DeterministicAnswerabilityClassifier,
    llm_classifier: LlmEvidenceSufficiencyClassifierV2,
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
        prediction = (
            outcome.decision.classification if outcome.decision is not None else None
        )
        expected.append(example.answerability)
        deterministic_predictions.append(deterministic.classification)
        llm_predictions.append(prediction)
        all_attempts.extend(outcome.attempts)
        actual_models.update(
            attempt.model for attempt in outcome.attempts if attempt.model is not None
        )
        assessment = (
            outcome.decision.assessment if outcome.decision is not None else None
        )
        report.append(
            {
                "id": example.id,
                "question": example.question,
                "ground_truth_label": example.answerability.value,
                "deterministic_prediction": deterministic.classification.value,
                "llm_derived_prediction": (
                    prediction.value if prediction is not None else None
                ),
                "deterministic_llm_agreement": (
                    prediction == deterministic.classification
                    if prediction is not None
                    else False
                ),
                "comparison_outcome": _comparison_outcome(
                    example.answerability,
                    deterministic.classification,
                    prediction,
                ),
                "llm_correct": prediction == example.answerability,
                "llm_cited_evidence_ids": _all_cited_ids(outcome),
                "llm_requested_claims": (
                    [
                        claim.model_dump(mode="json")
                        for claim in assessment.requested_claims
                    ]
                    if assessment is not None
                    else []
                ),
                "llm_premises": (
                    [premise.model_dump(mode="json") for premise in assessment.premises]
                    if assessment is not None
                    else []
                ),
                "llm_reason_codes": (
                    [code.value for code in assessment.reason_codes]
                    if assessment is not None
                    else []
                ),
                "llm_summary": assessment.summary if assessment is not None else None,
                "failure_category": (
                    "structured_output_failure" if outcome.failure is not None else None
                ),
                "error_category": _error_category(example, prediction, outcome),
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
        "classification_derivation": "deterministic_from_requested_claims_v2",
        "deterministic": deterministic_metrics,
        "llm_v2": {
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
                "llm_derived_prediction": row["llm_derived_prediction"],
                "failure_category": row["failure_category"],
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


def write_llm_answerability_v2_report(
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
