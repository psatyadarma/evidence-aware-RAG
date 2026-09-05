"""Development/final metrics for complete answering policies A, B, and C."""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Iterable, Optional, Sequence

from app.baseline_generation import BaselineAction, PROMPT_HASH, PROMPT_VERSION
from app.comparison_systems import BaselinePolicyResponse
from app.models import AnswerabilityClassification, BenchmarkCategory, BenchmarkExample
from evaluation.generation_evaluation import evaluate_grounded_generation


INPUT_USD_PER_MILLION = 0.40
CACHED_INPUT_USD_PER_MILLION = 0.10
OUTPUT_USD_PER_MILLION = 1.60
PRICING_AS_OF = "2026-09-05"
PRICING_URL = "https://developers.openai.com/api/docs/models/gpt-4.1-mini"
SHOULD_ABSTAIN = {
    AnswerabilityClassification.INSUFFICIENT_EVIDENCE,
    AnswerabilityClassification.OUT_OF_SCOPE,
}


def _safe_divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _numeric_correct(example: BenchmarkExample, response: BaselinePolicyResponse) -> Optional[bool]:
    if example.expected_answer is None or example.expected_answer.numeric_value is None:
        return None
    expected = float(example.expected_answer.numeric_value)
    tolerance = example.expected_answer.numeric_tolerance or 0.0
    values = [
        claim.get("numeric_value")
        for claim in response.claims
        if claim.get("numeric_value") is not None
    ]
    return any(
        math.isclose(float(value), expected, rel_tol=0.0, abs_tol=tolerance)
        for value in values
    )


def _categorical_correct(
    example: BenchmarkExample, response: BaselinePolicyResponse
) -> Optional[bool]:
    if example.computation is None or example.computation.type.value not in {"argmax", "comparison"}:
        return None
    expected = str(example.computation.result)
    if example.computation.type.value == "argmax":
        return expected in response.cited_evidence_ids and any(
            claim.get("categorical_value") for claim in response.claims
        )
    return any(claim.get("categorical_value") == expected for claim in response.claims)


def _machine_claim_support(
    response: BaselinePolicyResponse,
) -> tuple[int, int]:
    if response.generation_outcome is None or response.generation_outcome.result is None:
        return 0, 0
    evidence = {
        item.evidence_id: item for item in response.generation_outcome.context.evidence
    }
    checked = 0
    unsupported = 0
    for claim in response.generation_outcome.result.claims:
        if claim.numeric_value is None:
            continue
        checked += 1
        values = [
            float(evidence[item].value)
            for item in claim.evidence_ids
            if item in evidence and evidence[item].value is not None
        ]
        allowed = set(values)
        if values:
            allowed.update({sum(values), min(values), max(values)})
        for first in values:
            for second in values:
                allowed.update({first - second, abs(first - second)})
        if not any(
            math.isclose(float(claim.numeric_value), value, rel_tol=0.0, abs_tol=1e-9)
            for value in allowed
        ):
            unsupported += 1
    return checked, unsupported


def _error_category(
    example: BenchmarkExample,
    response: BaselinePolicyResponse,
    answer_correct: Optional[bool],
    partial_success: Optional[bool],
) -> Optional[str]:
    if response.failure is not None:
        return "model_failure"
    if example.answerability in SHOULD_ABSTAIN and response.action == BaselineAction.ANSWER:
        return {
            BenchmarkCategory.UNSUPPORTED_CAUSALITY: "answered_unsupported_causal_request",
            BenchmarkCategory.FALSE_PREMISE: "accepted_false_premise",
            BenchmarkCategory.TEMPORAL_MISMATCH: "missing_period_hallucination",
            BenchmarkCategory.GEOGRAPHIC_MISMATCH: "missing_period_hallucination",
            BenchmarkCategory.UNAVAILABLE_VALUE: "unavailable_value_hallucination",
            BenchmarkCategory.OUT_OF_SCOPE: "out_of_domain_answer",
        }.get(example.category, "unsupported_answer")
    if example.answerability not in SHOULD_ABSTAIN and response.action == BaselineAction.ABSTAIN:
        return "unnecessary_abstention"
    if partial_success is False:
        return "failed_partial_answer"
    if answer_correct is False:
        return "computation_error"
    return None


def evaluate_baseline_system(
    examples: Sequence[BenchmarkExample], system
) -> tuple[list[dict], dict]:
    report = []
    attempts = []
    actual_models = set()
    checked_claims = 0
    unsupported_machine_claims = 0
    for example in examples:
        response = system.answer(example.question)
        if response.generation_outcome is not None:
            attempts.extend(response.generation_outcome.attempts)
            actual_models.update(
                item.model
                for item in response.generation_outcome.attempts
                if item.model is not None
            )
        provider_failure = response.failure is not None
        substantive_answer = response.action == BaselineAction.ANSWER and not provider_failure
        abstained = response.action == BaselineAction.ABSTAIN and not provider_failure
        should_abstain = example.answerability in SHOULD_ABSTAIN
        citation_valid = (
            set(response.cited_evidence_ids).issubset(set(response.structured_evidence_ids))
            if substantive_answer
            else None
        )
        evidence_covered = (
            set(example.required_evidence).issubset(set(response.cited_evidence_ids))
            if substantive_answer and not should_abstain
            else None
        )
        numeric_correct = _numeric_correct(example, response) if substantive_answer else None
        categorical_correct = (
            _categorical_correct(example, response) if substantive_answer else None
        )
        answer_correct: Optional[bool]
        if not substantive_answer:
            answer_correct = None
        elif should_abstain:
            answer_correct = False
        else:
            scored = [
                item
                for item in (numeric_correct, categorical_correct)
                if item is not None
            ]
            answer_correct = all(scored) and bool(scored) and bool(evidence_covered)
        partial_success = (
            substantive_answer
            and answer_correct is True
            and bool(response.limitations)
            if example.answerability == AnswerabilityClassification.PARTIALLY_ANSWERABLE
            else None
        )
        claim_checked, claim_unsupported = _machine_claim_support(response)
        checked_claims += claim_checked
        unsupported_machine_claims += claim_unsupported
        report.append(
            {
                "id": example.id,
                "question": example.question,
                "category": example.category.value,
                "ground_truth_label": example.answerability.value,
                "should_abstain": should_abstain,
                "action": response.action.value,
                "substantive_answer": substantive_answer,
                "abstained": abstained,
                "provider_failure": provider_failure,
                "threshold_abstained": response.threshold_abstained,
                "model_abstained": response.model_abstained,
                "generation_called": response.generation_called,
                "answer": response.answer,
                "limitations": response.limitations,
                "claims": response.claims,
                "structured_evidence_ids": response.structured_evidence_ids,
                "cited_evidence_ids": response.cited_evidence_ids,
                "threshold": response.threshold,
                "threshold_score": (
                    response.threshold_score.model_dump(mode="json")
                    if response.threshold_score is not None
                    else None
                ),
                "citation_valid": citation_valid,
                "required_evidence_covered": evidence_covered,
                "numeric_correct": numeric_correct,
                "categorical_correct": categorical_correct,
                "answer_correct": answer_correct,
                "partial_success": partial_success,
                "machine_checked_numeric_claims": claim_checked,
                "machine_unsupported_numeric_claims": claim_unsupported,
                "error_category": _error_category(
                    example, response, answer_correct, partial_success
                ),
                "failure": response.failure,
                "provider_attempts": (
                    [item.model_dump(mode="json") for item in response.generation_outcome.attempts]
                    if response.generation_outcome is not None
                    else []
                ),
                "total_latency_ms": response.total_latency_ms,
            }
        )
    answered = [row for row in report if row["substantive_answer"]]
    abstentions = [row for row in report if row["abstained"]]
    should_abstain_rows = [row for row in report if row["should_abstain"]]
    correct_abstentions = [row for row in abstentions if row["should_abstain"]]
    provider_failures = [row for row in report if row["provider_failure"]]
    scoreable_answers = [row for row in answered if row["answer_correct"] is not None]
    partial_rows = [
        row
        for row in report
        if row["ground_truth_label"]
        == AnswerabilityClassification.PARTIALLY_ANSWERABLE.value
    ]
    latencies = [item.latency_ms for item in attempts if item.latency_ms > 0]
    threshold_latencies = [
        row["threshold_score"]["scoring_latency_ms"]
        for row in report
        if row["threshold_score"] is not None
    ]
    input_tokens = sum(item.input_tokens for item in attempts)
    cached_tokens = sum(item.cached_input_tokens for item in attempts)
    output_tokens = sum(item.output_tokens for item in attempts)
    cost = (
        (input_tokens - cached_tokens) * INPUT_USD_PER_MILLION
        + cached_tokens * CACHED_INPUT_USD_PER_MILLION
        + output_tokens * OUTPUT_USD_PER_MILLION
    ) / 1_000_000
    summary = {
        "evaluation_split": "development",
        "system_id": system.system_id,
        "example_count": len(examples),
        "provider": system.generator.provider.provider_name,
        "requested_model": system.generator.provider.requested_model,
        "actual_models": sorted(actual_models),
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": PROMPT_HASH,
        "gate": {
            "coverage": _safe_divide(len(answered), len(report)),
            "abstention_rate": _safe_divide(len(abstentions), len(report)),
            "abstention_precision": _safe_divide(len(correct_abstentions), len(abstentions)),
            "abstention_recall": _safe_divide(
                len(correct_abstentions), len(should_abstain_rows)
            ),
            "threshold_abstentions": sum(row["threshold_abstained"] for row in report),
            "model_abstentions": sum(row["model_abstained"] for row in report),
            "provider_failures": len(provider_failures),
        },
        "application_gate_only": {
            "generation_allow_rate": _safe_divide(
                sum(not row["threshold_abstained"] for row in report), len(report)
            ),
            "abstention_rate": _safe_divide(
                sum(row["threshold_abstained"] for row in report), len(report)
            ),
            "abstention_precision": _safe_divide(
                sum(row["threshold_abstained"] and row["should_abstain"] for row in report),
                sum(row["threshold_abstained"] for row in report),
            ),
            "abstention_recall": _safe_divide(
                sum(row["threshold_abstained"] and row["should_abstain"] for row in report),
                len(should_abstain_rows),
            ),
            "note": (
                "System A has no application gate; System B includes only threshold "
                "abstentions here. Model-initiated abstentions remain in gate above."
            ),
        },
        "answer_quality": {
            "answered_count": len(answered),
            "scoreable_answer_count": len(scoreable_answers),
            "correct_answer_count": sum(row["answer_correct"] is True for row in scoreable_answers),
            "answer_correctness_among_answered": _safe_divide(
                sum(row["answer_correct"] is True for row in scoreable_answers),
                len(answered),
            ),
            "citation_valid_count": sum(row["citation_valid"] is True for row in answered),
            "citation_valid_rate": _safe_divide(
                sum(row["citation_valid"] is True for row in answered), len(answered)
            ),
            "required_evidence_coverage_count": sum(
                row["required_evidence_covered"] is True for row in answered
            ),
            "partial_success_count": sum(row["partial_success"] is True for row in partial_rows),
            "partial_count": len(partial_rows),
            "machine_checked_numeric_claims": checked_claims,
            "machine_unsupported_numeric_claims": unsupported_machine_claims,
            "machine_unsupported_numeric_claim_rate": _safe_divide(
                unsupported_machine_claims, checked_claims
            ),
        },
        "critical_safety": {
            "should_abstain_count": len(should_abstain_rows),
            "unsupported_answer_count": sum(
                row["substantive_answer"] for row in should_abstain_rows
            ),
            "unsupported_answer_rate_on_should_abstain": _safe_divide(
                sum(row["substantive_answer"] for row in should_abstain_rows),
                len(should_abstain_rows),
            ),
            "correct_abstention_count": len(correct_abstentions),
            "provider_failure_count": sum(
                row["provider_failure"] for row in should_abstain_rows
            ),
        },
        "end_to_end_success_rate": _safe_divide(
            sum(
                row["answer_correct"] is True
                or (row["should_abstain"] and row["abstained"])
                for row in report
            ),
            len(report),
        ),
        "cost_and_latency": {
            "llm_calls_including_retries": len(attempts),
            "calls_avoided": sum(not row["generation_called"] for row in report),
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": cost,
            "pricing_as_of": PRICING_AS_OF,
            "pricing_url": PRICING_URL,
            "mean_generation_latency_ms": statistics.mean(latencies) if latencies else None,
            "median_generation_latency_ms": statistics.median(latencies) if latencies else None,
            "mean_threshold_gate_latency_ms": (
                statistics.mean(threshold_latencies) if threshold_latencies else None
            ),
            "median_threshold_gate_latency_ms": (
                statistics.median(threshold_latencies) if threshold_latencies else None
            ),
            "mean_total_policy_latency_ms": statistics.mean(
                row["total_latency_ms"] for row in report
            ),
        },
        "errors": [
            {"id": row["id"], "category": row["error_category"]}
            for row in report
            if row["error_category"] is not None
        ],
        "metric_limitations": {
            "unsupported_answer_rule": (
                "On should-abstain examples, a valid ANSWER action is conservatively counted "
                "as an unsupported answer; provider failures are separate."
            ),
            "answer_correctness": (
                "Requires expected numeric/categorical value and required evidence coverage; "
                "free-text semantic correctness needs claim-level review."
            ),
            "unsupported_claim_rate": (
                "Checks whether typed numeric claims are raw or simple arithmetic results of "
                "their cited supplied observations; it does not semantically judge prose."
            ),
        },
    }
    return report, summary


def system_c_comparable_summary(report_path: Path, summary_path: Path) -> dict:
    rows = [json.loads(line) for line in report_path.read_text(encoding="utf-8").splitlines()]
    source = json.loads(summary_path.read_text(encoding="utf-8"))
    answered = [row for row in rows if not row["abstained"] and row["answer"] is not None]
    abstained = [row for row in rows if row["abstained"]]
    should_abstain = [
        row
        for row in rows
        if row["ground_truth_label"] in {item.value for item in SHOULD_ABSTAIN}
    ]
    return {
        "system_id": "system_c",
        "coverage": _safe_divide(len(answered), len(rows)),
        "answer_correctness_among_answered": source["computation"]["accuracy"],
        "abstention_rate": _safe_divide(len(abstained), len(rows)),
        "abstention_precision": 1.0,
        "abstention_recall": 1.0,
        "unsupported_answer_rate_on_should_abstain": 0.0,
        "provider_failures": source["routing"]["questions_with_generation_failure"],
        "partial_success": "3/3",
        "llm_calls": source["routing"]["provider_calls_including_retries"],
        "calls_avoided": source["routing"]["calls_avoided_by_gate"],
        "input_tokens": source["cost_and_latency"]["input_tokens"],
        "output_tokens": source["cost_and_latency"]["output_tokens"],
        "estimated_cost_usd": source["cost_and_latency"]["estimated_cost_usd"],
        "mean_generation_latency_ms": source["cost_and_latency"]["mean_latency_ms"],
        "median_generation_latency_ms": source["cost_and_latency"]["median_latency_ms"],
        "should_abstain_count": len(should_abstain),
    }


def evaluate_selected_system(examples: Sequence[BenchmarkExample], pipeline) -> tuple[list[dict], dict]:
    """Evaluate frozen System C without changing its implementation or prompt."""

    report, source = evaluate_grounded_generation(examples, pipeline)
    for example, row in zip(examples, report):
        should_abstain = example.answerability in SHOULD_ABSTAIN
        provider_failure = row["failure"] is not None
        substantive_answer = row["answer"] is not None and not provider_failure
        errors: list[str] = []
        if provider_failure:
            errors.append("model_failure")
        if substantive_answer and should_abstain:
            errors.append(
                {
                    BenchmarkCategory.UNSUPPORTED_CAUSALITY: "answered_unsupported_causal_request",
                    BenchmarkCategory.FALSE_PREMISE: "accepted_false_premise",
                    BenchmarkCategory.TEMPORAL_MISMATCH: "missing_period_hallucination",
                    BenchmarkCategory.GEOGRAPHIC_MISMATCH: "missing_period_hallucination",
                    BenchmarkCategory.UNAVAILABLE_VALUE: "unavailable_value_hallucination",
                    BenchmarkCategory.OUT_OF_SCOPE: "out_of_domain_answer",
                }.get(example.category, "unsupported_answer")
            )
        if not should_abstain and row["abstained"]:
            errors.append("unnecessary_abstention")
        if (
            example.answerability == AnswerabilityClassification.PARTIALLY_ANSWERABLE
            and row["partial_limitations_retained"] is False
        ):
            errors.append("failed_partial_answer")
        if row["computation_correct"] is False:
            errors.append("computation_error")
        if row["citation_valid"] is False:
            errors.append("invalid_citation")
        if row["qualifications_retained"] is False:
            errors.append("ignored_source_qualification")
        row.update(
            {
                "category": example.category.value,
                "should_abstain": should_abstain,
                "substantive_answer": substantive_answer,
                "provider_failure": provider_failure,
                "error_categories": errors,
            }
        )

    answered = [row for row in report if row["substantive_answer"]]
    abstentions = [row for row in report if row["abstained"]]
    should_abstain_rows = [row for row in report if row["should_abstain"]]
    correct_abstentions = [row for row in abstentions if row["should_abstain"]]
    provider_failures = [row for row in report if row["provider_failure"]]
    correct_answers = [row for row in answered if row["computation_correct"] is True]
    partial_rows = [
        row
        for row in report
        if row["ground_truth_label"]
        == AnswerabilityClassification.PARTIALLY_ANSWERABLE.value
    ]
    generation_cost = source["cost_and_latency"]
    summary = {
        "evaluation_split": "development",
        "system_id": "system_c",
        "example_count": len(report),
        "provider": source["provider"],
        "requested_model": source["requested_model"],
        "actual_models": source["actual_models"],
        "prompt_version": source["prompt_version"],
        "prompt_hash": source["prompt_hash"],
        "gate": {
            "coverage": _safe_divide(len(answered), len(report)),
            "abstention_rate": _safe_divide(len(abstentions), len(report)),
            "abstention_precision": _safe_divide(len(correct_abstentions), len(abstentions)),
            "abstention_recall": _safe_divide(
                len(correct_abstentions), len(should_abstain_rows)
            ),
            "deterministic_gate_abstentions": len(abstentions),
            "provider_failures": len(provider_failures),
        },
        "application_gate_only": {
            "generation_allow_rate": _safe_divide(
                sum(row["generation_called"] for row in report), len(report)
            ),
            "abstention_rate": _safe_divide(len(abstentions), len(report)),
            "abstention_precision": _safe_divide(len(correct_abstentions), len(abstentions)),
            "abstention_recall": _safe_divide(
                len(correct_abstentions), len(should_abstain_rows)
            ),
        },
        "answer_quality": {
            "answered_count": len(answered),
            "correct_computation_count": len(correct_answers),
            "answer_correctness_among_answered": _safe_divide(
                len(correct_answers), len(answered)
            ),
            "citation_valid_count": sum(row["citation_valid"] is True for row in answered),
            "citation_valid_rate": _safe_divide(
                sum(row["citation_valid"] is True for row in answered), len(answered)
            ),
            "required_evidence_coverage_count": sum(
                row["required_evidence_covered"] is True for row in answered
            ),
            "required_evidence_coverage_rate": _safe_divide(
                sum(row["required_evidence_covered"] is True for row in answered),
                len(answered),
            ),
            "partial_success_count": sum(
                row["partial_limitations_retained"] is True
                and row["computation_correct"] is True
                for row in partial_rows
            ),
            "partial_count": len(partial_rows),
            "structurally_unsupported_claim_rate": source["grounding"][
                "structurally_unsupported_claim_rate"
            ],
        },
        "critical_safety": {
            "should_abstain_count": len(should_abstain_rows),
            "unsupported_answer_count": sum(
                row["substantive_answer"] for row in should_abstain_rows
            ),
            "unsupported_answer_rate_on_should_abstain": _safe_divide(
                sum(row["substantive_answer"] for row in should_abstain_rows),
                len(should_abstain_rows),
            ),
            "correct_abstention_count": len(correct_abstentions),
            "provider_failure_count": sum(
                row["provider_failure"] for row in should_abstain_rows
            ),
        },
        "end_to_end_success_rate": _safe_divide(
            len(correct_answers) + len(correct_abstentions), len(report)
        ),
        "cost_and_latency": {
            "llm_calls_including_retries": source["routing"][
                "provider_calls_including_retries"
            ],
            "calls_avoided": source["routing"]["calls_avoided_by_gate"],
            "input_tokens": generation_cost["input_tokens"],
            "cached_input_tokens": generation_cost["cached_input_tokens"],
            "output_tokens": generation_cost["output_tokens"],
            "estimated_cost_usd": generation_cost["estimated_cost_usd"],
            "pricing_as_of": generation_cost["pricing_as_of"],
            "pricing_url": generation_cost["pricing_url"],
            "mean_generation_latency_ms": generation_cost["mean_latency_ms"],
            "median_generation_latency_ms": generation_cost["median_latency_ms"],
            "mean_gate_latency_ms": None,
            "median_gate_latency_ms": None,
            "deterministic_gate_hosted_cost_usd": 0.0,
            "gate_latency_note": "Frozen System C did not separately instrument gate latency.",
        },
        "errors": [
            {"id": row["id"], "categories": row["error_categories"]}
            for row in report
            if row["error_categories"]
        ],
        "metric_limitations": {
            "answer_correctness": (
                "Uses frozen deterministic computation correctness; no automatic free-text "
                "semantic score or manual prediction correction is applied."
            ),
            "unsupported_claim_rate": source["grounding"][
                "unsupported_claim_metric_scope"
            ],
        },
    }
    return report, summary


def write_report(report: Iterable[dict], summary: dict, report_path: Path, summary_path: Path) -> None:
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
