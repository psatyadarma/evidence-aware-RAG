"""Pure evaluation-only metric derivation from frozen final JSONL records."""

from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Sequence


SHOULD_ABSTAIN = {"INSUFFICIENT_EVIDENCE", "OUT_OF_SCOPE"}
PARTIAL = "PARTIALLY_ANSWERABLE"
INPUT_USD_PER_MILLION = 0.40
CACHED_INPUT_USD_PER_MILLION = 0.10
OUTPUT_USD_PER_MILLION = 1.60


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _generated(row: dict) -> bool:
    """Use explicit execution/action fields, never response-string length."""

    return bool(
        row.get("generation_called")
        and not row.get("abstained")
        and not row.get("provider_failure")
        and row.get("failure") is None
    )


def _correct(row: dict, system_id: str) -> bool:
    field = "computation_correct" if system_id == "system_c" else "answer_correct"
    return _generated(row) and row.get(field) is True


def _partial_success(row: dict, system_id: str) -> bool:
    if row.get("ground_truth_label") != PARTIAL or not _generated(row):
        return False
    if system_id == "system_c":
        return (
            row.get("computation_correct") is True
            and row.get("partial_limitations_retained") is True
        )
    return row.get("partial_success") is True


def _system_c_errors(row: dict) -> list[str]:
    generated = _generated(row)
    should_abstain = bool(row.get("should_abstain"))
    errors: list[str] = []
    if row.get("provider_failure"):
        errors.append("model_failure")
    if generated and should_abstain:
        errors.append(
            {
                "unsupported_causality": "answered_unsupported_causal_request",
                "false_premise": "accepted_false_premise",
                "temporal_mismatch": "missing_period_hallucination",
                "geographic_mismatch": "missing_period_hallucination",
                "unavailable_value": "unavailable_value_hallucination",
                "out_of_scope": "out_of_domain_answer",
            }.get(row.get("category"), "unsupported_answer")
        )
    if row.get("abstained") and not should_abstain:
        errors.append("unnecessary_abstention")
    if row.get("ground_truth_label") == PARTIAL and not _partial_success(row, "system_c"):
        errors.append("failed_partial_answer")
    if generated and row.get("computation_correct") is False:
        errors.append("computation_error")
    if generated and row.get("citation_valid") is False:
        errors.append("invalid_citation")
    if generated and row.get("qualifications_retained") is False:
        errors.append("ignored_source_qualification")
    return errors


def _cost_and_latency(rows: Sequence[dict], system_id: str) -> dict:
    attempts = [attempt for row in rows for attempt in row.get("provider_attempts", [])]
    input_tokens = sum(int(item.get("input_tokens", 0)) for item in attempts)
    cached_tokens = sum(int(item.get("cached_input_tokens", 0)) for item in attempts)
    output_tokens = sum(int(item.get("output_tokens", 0)) for item in attempts)
    generation_latencies = [
        float(item["latency_ms"])
        for item in attempts
        if float(item.get("latency_ms", 0.0)) > 0.0
    ]
    threshold_latencies = [
        float(row["threshold_score"]["scoring_latency_ms"])
        for row in rows
        if row.get("threshold_score") is not None
    ]
    total_latencies = [
        float(row["total_latency_ms"])
        for row in rows
        if row.get("total_latency_ms") is not None
    ]
    estimated_cost = (
        (input_tokens - cached_tokens) * INPUT_USD_PER_MILLION
        + cached_tokens * CACHED_INPUT_USD_PER_MILLION
        + output_tokens * OUTPUT_USD_PER_MILLION
    ) / 1_000_000
    return {
        "llm_calls_including_retries": len(attempts),
        "calls_avoided": sum(not row.get("generation_called") for row in rows),
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost,
        "mean_generation_latency_ms": (
            statistics.mean(generation_latencies) if generation_latencies else None
        ),
        "median_generation_latency_ms": (
            statistics.median(generation_latencies) if generation_latencies else None
        ),
        "mean_gate_latency_ms": (
            statistics.mean(threshold_latencies) if threshold_latencies else None
        ),
        "median_gate_latency_ms": (
            statistics.median(threshold_latencies) if threshold_latencies else None
        ),
        "mean_total_policy_latency_ms": (
            statistics.mean(total_latencies) if total_latencies else None
        ),
        "deterministic_gate_hosted_cost_usd": 0.0 if system_id == "system_c" else None,
        "pricing": {
            "standard_input_usd_per_million": INPUT_USD_PER_MILLION,
            "cached_input_usd_per_million": CACHED_INPUT_USD_PER_MILLION,
            "output_usd_per_million": OUTPUT_USD_PER_MILLION,
        },
    }


def derive_metrics(rows: Sequence[dict], system_id: str) -> dict:
    if system_id not in {"system_a", "system_b", "system_c"}:
        raise ValueError(f"unknown system: {system_id}")
    if not rows:
        raise ValueError("cannot evaluate an empty report")
    ids = [row.get("id") for row in rows]
    if None in ids or len(ids) != len(set(ids)):
        raise ValueError("report IDs must be present and unique")

    generated = [row for row in rows if _generated(row)]
    abstained = [
        row
        for row in rows
        if row.get("abstained") and not row.get("provider_failure")
    ]
    should_abstain = [row for row in rows if row.get("should_abstain")]
    correct_abstentions = [row for row in abstained if row.get("should_abstain")]
    unsupported = [row for row in generated if row.get("should_abstain")]
    correct = [row for row in generated if _correct(row, system_id)]
    provider_failures = [row for row in rows if row.get("provider_failure")]
    partial = [row for row in rows if row.get("ground_truth_label") == PARTIAL]
    partial_successes = [row for row in partial if _partial_success(row, system_id)]
    citation_defined = [row for row in generated if row.get("citation_valid") is not None]
    evidence_defined = [
        row for row in generated if row.get("required_evidence_covered") is not None
    ]
    qualification_defined = [
        row for row in generated if row.get("qualifications_retained") is not None
    ]
    if system_id == "system_c":
        error_rows = [
            {"id": row["id"], "categories": _system_c_errors(row)}
            for row in rows
            if _system_c_errors(row)
        ]
    else:
        error_rows = [
            {"id": row["id"], "categories": [row["error_category"]]}
            for row in rows
            if row.get("error_category")
        ]
    error_counts = Counter(
        category for row in error_rows for category in row["categories"]
    )
    return {
        "system_id": system_id,
        "example_count": len(rows),
        "generated_answers": {
            "count": len(generated),
            "coverage": _rate(len(generated), len(rows)),
            "semantic_rule": (
                "generation_called=true, abstained=false, and no provider failure; "
                "response-string length is ignored"
            ),
        },
        "abstention": {
            "count": len(abstained),
            "rate": _rate(len(abstained), len(rows)),
            "correct_count": len(correct_abstentions),
            "precision": _rate(len(correct_abstentions), len(abstained)),
            "recall": _rate(len(correct_abstentions), len(should_abstain)),
            "should_abstain_count": len(should_abstain),
        },
        "unsupported_answers": {
            "count": len(unsupported),
            "rate_on_should_abstain": _rate(len(unsupported), len(should_abstain)),
            "example_ids": [row["id"] for row in unsupported],
        },
        "correctness_among_generated": {
            "correct_count": len(correct),
            "generated_count": len(generated),
            "rate": _rate(len(correct), len(generated)),
            "scope": (
                "frozen computation_correct" if system_id == "system_c" else
                "frozen machine-checkable answer_correct"
            ),
        },
        "citations": {
            "valid_count": sum(row.get("citation_valid") is True for row in citation_defined),
            "defined_count": len(citation_defined),
            "valid_rate": _rate(
                sum(row.get("citation_valid") is True for row in citation_defined),
                len(citation_defined),
            ),
        },
        "required_evidence": {
            "covered_count": sum(
                row.get("required_evidence_covered") is True for row in evidence_defined
            ),
            "defined_count": len(evidence_defined),
            "coverage_rate": _rate(
                sum(row.get("required_evidence_covered") is True for row in evidence_defined),
                len(evidence_defined),
            ),
        },
        "partial_answers": {
            "success_count": len(partial_successes),
            "ground_truth_partial_count": len(partial),
            "success_rate": _rate(len(partial_successes), len(partial)),
            "generated_partial_count": sum(_generated(row) for row in partial),
            "success_rate_among_generated_partial": _rate(
                len(partial_successes), sum(_generated(row) for row in partial)
            ),
        },
        "source_qualifications": {
            "retained_count": sum(
                row.get("qualifications_retained") is True for row in qualification_defined
            ),
            "defined_count": len(qualification_defined),
            "retention_rate": (
                _rate(
                    sum(
                        row.get("qualifications_retained") is True
                        for row in qualification_defined
                    ),
                    len(qualification_defined),
                )
                if qualification_defined
                else None
            ),
            "note": (
                None if qualification_defined else
                "The frozen baseline report did not define a machine qualification field."
            ),
        },
        "provider_failures": {
            "count": len(provider_failures),
            "example_ids": [row["id"] for row in provider_failures],
        },
        "end_to_end_success": {
            "count": len(correct) + len(correct_abstentions),
            "rate": _rate(len(correct) + len(correct_abstentions), len(rows)),
            "scope": (
                "Frozen machine-checkable answer/computation correctness plus correct full "
                "abstention; strict partial success is reported separately."
            ),
        },
        "cost_and_latency": _cost_and_latency(rows, system_id),
        "errors": {
            "counts": dict(sorted(error_counts.items())),
            "records": error_rows,
            "unnecessary_abstention_ids": [
                row["id"]
                for row in rows
                if row.get("abstained") and not row.get("should_abstain")
            ],
        },
    }


def assert_system_c_sanity(metrics: dict) -> None:
    observed = {
        "example_count": metrics["example_count"],
        "generation_called": metrics["generated_answers"]["count"],
        "deterministic_abstentions": metrics["abstention"]["count"],
        "should_abstain": metrics["abstention"]["should_abstain_count"],
        "correct_should_abstain": metrics["abstention"]["correct_count"],
        "unsupported_ids": metrics["unsupported_answers"]["example_ids"],
        "correct_computations": metrics["correctness_among_generated"]["correct_count"],
        "valid_citations": metrics["citations"]["valid_count"],
    }
    expected = {
        "example_count": 45,
        "generation_called": 17,
        "deterministic_abstentions": 28,
        "should_abstain": 20,
        "correct_should_abstain": 19,
        "unsupported_ids": ["t029"],
        "correct_computations": 14,
        "valid_citations": 17,
    }
    if observed != expected:
        raise ValueError(f"System C raw sanity checks disagree: {observed!r}")


def finite_or_none(value: object) -> bool:
    """Small schema helper used by offline tests."""

    return value is None or (isinstance(value, (int, float)) and math.isfinite(value))
