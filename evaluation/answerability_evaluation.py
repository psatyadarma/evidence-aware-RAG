"""Development-only measurement for deterministic evidence sufficiency."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from app.answerability import (
    DecisionReason,
    DeterministicAnswerabilityClassifier,
    DeterministicAnswerabilityDecision,
)
from app.models import AnswerabilityClassification, BenchmarkExample
from evaluation.metrics import answerability_metrics, abstention_metrics


def _general_error_cause(
    expected: AnswerabilityClassification,
    decision: DeterministicAnswerabilityDecision,
) -> str:
    reasons = set(decision.reasons)
    if DecisionReason.QUERY_NOT_RESOLVED in reasons:
        return "parsing_failure"
    if DecisionReason.AMBIGUOUS_GEOGRAPHY in reasons:
        return "ambiguity"
    if DecisionReason.INCOMPATIBLE_COMPARISON in reasons:
        return "unsupported_operation_handling"
    if DecisionReason.FALSE_PREMISE in reasons:
        return "premise_checking_policy_disagreement"
    if expected == AnswerabilityClassification.PARTIALLY_ANSWERABLE:
        return "claim_decomposition_failure"
    if any(
        reason
        in {
            DecisionReason.REQUESTED_YEAR_UNAVAILABLE,
            DecisionReason.REQUESTED_GEOGRAPHY_UNAVAILABLE,
            DecisionReason.VALUE_NOT_AVAILABLE,
            DecisionReason.BOUNDARY_DEFINITION_CHANGE,
        }
        for reason in reasons
    ):
        return "missing_schema_rule_or_label_policy_disagreement"
    return "label_policy_disagreement"


def evaluate_answerability(
    examples: Sequence[BenchmarkExample],
    classifier: DeterministicAnswerabilityClassifier,
) -> tuple[list[dict], dict]:
    report: list[dict] = []
    expected: list[AnswerabilityClassification] = []
    predicted: list[AnswerabilityClassification] = []

    for example in examples:
        decision = classifier.decide(example.question)
        expected.append(example.answerability)
        predicted.append(decision.classification)
        correct = decision.classification == example.answerability
        row = {
            "id": example.id,
            "question": example.question,
            "expected_classification": example.answerability.value,
            "predicted_classification": decision.classification.value,
            "correct": correct,
            "structured_interpretation": decision.structured_query.model_dump(mode="json"),
            "retrieved_evidence_ids": decision.retrieved_evidence_ids,
            "decision_reasons": [reason.value for reason in decision.reasons],
            "claims": [claim.model_dump(mode="json") for claim in decision.claims],
            "premise_check": (
                decision.premise_check.model_dump(mode="json")
                if decision.premise_check is not None
                else None
            ),
            "general_error_cause": (
                None
                if correct
                else _general_error_cause(example.answerability, decision)
            ),
        }
        report.append(row)

    metrics = answerability_metrics(expected, predicted)
    abstention = abstention_metrics(
        expected,
        [
            value
            in {
                AnswerabilityClassification.INSUFFICIENT_EVIDENCE,
                AnswerabilityClassification.OUT_OF_SCOPE,
            }
            for value in predicted
        ],
    )
    labels = list(AnswerabilityClassification)
    confusion = {
        actual.value: {
            prediction.value: sum(
                gold == actual and guessed == prediction
                for gold, guessed in zip(expected, predicted)
            )
            for prediction in labels
        }
        for actual in labels
    }
    summary = {
        "evaluation_split": "development",
        "example_count": len(examples),
        "accuracy": metrics.accuracy,
        "macro_f1": metrics.macro_f1,
        "per_class": {
            label.value: {
                "precision": value.precision,
                "recall": value.recall,
                "f1": value.f1,
                "support": value.support,
            }
            for label, value in metrics.per_class.items()
        },
        "confusion_matrix": confusion,
        "abstention": {
            "precision": abstention.precision,
            "recall": abstention.recall,
            "true_positives": abstention.true_positives,
            "predicted_abstentions": abstention.predicted_abstentions,
            "required_abstentions": abstention.required_abstentions,
        },
        "error_count": sum(not row["correct"] for row in report),
        "errors": [
            {
                "id": row["id"],
                "predicted_classification": row["predicted_classification"],
                "expected_classification": row["expected_classification"],
                "structured_interpretation": row["structured_interpretation"],
                "retrieved_evidence_ids": row["retrieved_evidence_ids"],
                "decision_reasons": row["decision_reasons"],
                "general_error_cause": row["general_error_cause"],
            }
            for row in report
            if not row["correct"]
        ],
    }
    return report, summary


def write_answerability_report(
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
                output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
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
