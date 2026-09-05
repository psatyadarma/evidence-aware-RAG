"""Development-only audit of gated deterministic computation and generation."""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

from app.computation import VerifiedFact
from app.generation import PROMPT_HASH, PROMPT_VERSION
from app.models import AnswerabilityClassification, BenchmarkExample
from app.pipeline import EvidenceAwarePipeline


STANDARD_INPUT_USD_PER_MILLION = 0.40
CACHED_INPUT_USD_PER_MILLION = 0.10
OUTPUT_USD_PER_MILLION = 1.60
PRICING_AS_OF = "2026-09-05"
PRICING_URL = "https://developers.openai.com/api/docs/models/gpt-4.1-mini"
GENERATING_CLASSES = {
    AnswerabilityClassification.ANSWERABLE,
    AnswerabilityClassification.PARTIALLY_ANSWERABLE,
}


def _safe_divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _numeric_equal(
    actual: Optional[Union[int, float]],
    expected: Union[int, float],
    tolerance: float = 0.0,
) -> bool:
    return actual is not None and math.isclose(
        float(actual), float(expected), rel_tol=0.0, abs_tol=tolerance
    )


def _fact_correct(example: BenchmarkExample, fact: Optional[VerifiedFact]) -> Optional[bool]:
    if fact is None:
        return False if example.answerability in GENERATING_CLASSES else None
    if example.computation is not None:
        expected = example.computation.result
        if example.computation.type.value == "argmax":
            return fact.result_evidence_id == expected
        if example.computation.type.value == "comparison":
            return fact.categorical_value == expected
        assert isinstance(expected, (int, float))
        return _numeric_equal(fact.numeric_value, expected)
    if example.expected_answer is not None and example.expected_answer.numeric_value is not None:
        return _numeric_equal(
            fact.numeric_value,
            example.expected_answer.numeric_value,
            example.expected_answer.numeric_tolerance or 0.0,
        )
    return None


def evaluate_grounded_generation(
    examples: Sequence[BenchmarkExample],
    pipeline: EvidenceAwarePipeline,
) -> tuple[list[dict], dict]:
    """Run only supplied examples; callers own split selection and live-call consent."""

    report: list[dict] = []
    attempts = []
    actual_models: set[str] = set()
    for example in examples:
        response = pipeline.answer(example.question)
        outcome = response.generation_outcome
        if outcome is not None:
            attempts.extend(outcome.attempts)
            actual_models.update(
                attempt.model for attempt in outcome.attempts if attempt.model is not None
            )
        generated = response.generated
        fact = response.computation.facts[0] if response.computation is not None else None
        cited_ids = (
            list(
                dict.fromkeys(
                    evidence_id
                    for claim in generated.claims
                    for evidence_id in claim.evidence_ids
                )
            )
            if generated is not None
            else []
        )
        provided_ids = (
            [item.evidence_id for item in outcome.context.evidence]
            if outcome is not None
            else []
        )
        citation_valid = (
            all(item in provided_ids for item in cited_ids)
            and all(
                claim.evidence_ids == fact_item.evidence_ids
                for claim in (generated.claims if generated is not None else [])
                for fact_item in (
                    [next(f for f in outcome.context.verified_facts if f.fact_id == claim.fact_id)]
                    if outcome is not None
                    else []
                )
            )
            if generated is not None
            else None
        )
        evidence_coverage = (
            set(example.required_evidence).issubset(set(cited_ids))
            if generated is not None
            else None
        )
        qualification_ids = (
            [item.qualification_id for item in outcome.context.qualifications]
            if outcome is not None
            else []
        )
        qualifications_retained = (
            generated.qualification_ids == qualification_ids
            and all(
                item.text in generated.answer for item in outcome.context.qualifications
            )
            if generated is not None
            else None
        )
        unsupported_ids = (
            [item.component_id for item in outcome.context.unsupported_components]
            if outcome is not None
            else []
        )
        partial_limitations_retained = (
            generated.unsupported_component_ids_addressed == unsupported_ids
            and all(
                item.explanation in generated.answer
                for item in outcome.context.unsupported_components
            )
            if response.classification == AnswerabilityClassification.PARTIALLY_ANSWERABLE
            and generated is not None
            else None
        )
        typed_claims_supported = (
            all(
                claim.fact_id in {item.fact_id for item in outcome.context.verified_facts}
                and claim.evidence_ids
                == next(
                    item.evidence_ids
                    for item in outcome.context.verified_facts
                    if item.fact_id == claim.fact_id
                )
                and claim.numeric_value
                == next(
                    item.numeric_value
                    for item in outcome.context.verified_facts
                    if item.fact_id == claim.fact_id
                )
                and claim.categorical_value
                == next(
                    item.categorical_value
                    for item in outcome.context.verified_facts
                    if item.fact_id == claim.fact_id
                )
                for claim in generated.claims
            )
            if generated is not None and outcome is not None
            else None
        )
        report.append(
            {
                "id": example.id,
                "question": example.question,
                "ground_truth_label": example.answerability.value,
                "gate_decision": response.classification.value,
                "generation_expected_from_gate": response.classification in GENERATING_CLASSES,
                "generation_called": response.generation_called,
                "call_avoided": not response.generation_called,
                "abstained": response.abstained,
                "answer": response.answer,
                "failure": response.failure,
                "computation": (
                    response.computation.model_dump(mode="json")
                    if response.computation is not None
                    else None
                ),
                "computation_correct": _fact_correct(example, fact),
                "generated_claims": (
                    [item.model_dump(mode="json") for item in generated.claims]
                    if generated is not None
                    else []
                ),
                "provided_evidence_ids": provided_ids,
                "cited_evidence_ids": cited_ids,
                "citation_valid": citation_valid,
                "required_evidence_covered": evidence_coverage,
                "required_qualification_ids": qualification_ids,
                "qualifications_retained": qualifications_retained,
                "unsupported_components": (
                    [item.model_dump(mode="json") for item in outcome.context.unsupported_components]
                    if outcome is not None
                    else []
                ),
                "partial_limitations_retained": partial_limitations_retained,
                "typed_claims_supported": typed_claims_supported,
                "provider_attempts": (
                    [item.model_dump(mode="json") for item in outcome.attempts]
                    if outcome is not None
                    else []
                ),
            }
        )

    generation_rows = [row for row in report if row["generation_called"]]
    valid_rows = [row for row in generation_rows if row["answer"] is not None]
    scoreable_computations = [
        row for row in generation_rows if row["computation_correct"] is not None
    ]
    claim_count = sum(len(row["generated_claims"]) for row in valid_rows)
    structurally_unsupported_claims = sum(
        not row["typed_claims_supported"]
        for row in valid_rows
        if row["typed_claims_supported"] is not None
    )
    latency_values = [attempt.latency_ms for attempt in attempts if attempt.latency_ms > 0]
    input_tokens = sum(attempt.input_tokens for attempt in attempts)
    cached_input_tokens = sum(attempt.cached_input_tokens for attempt in attempts)
    output_tokens = sum(attempt.output_tokens for attempt in attempts)
    estimated_cost = (
        (input_tokens - cached_input_tokens) * STANDARD_INPUT_USD_PER_MILLION
        + cached_input_tokens * CACHED_INPUT_USD_PER_MILLION
        + output_tokens * OUTPUT_USD_PER_MILLION
    ) / 1_000_000
    summary = {
        "evaluation_split": "development",
        "example_count": len(examples),
        "gate_architecture": "deterministic_answerability_before_generation",
        "provider": pipeline.generator.provider.provider_name,
        "requested_model": pipeline.generator.provider.requested_model,
        "actual_models": sorted(actual_models),
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": PROMPT_HASH,
        "generation_parameters": {
            "temperature": pipeline.generator.temperature,
            "max_output_tokens": pipeline.generator.max_output_tokens,
            "max_attempts": pipeline.generator.max_attempts,
        },
        "routing": {
            "generation_calls_expected": sum(
                row["generation_expected_from_gate"] for row in report
            ),
            "questions_sent_to_generator": len(generation_rows),
            "questions_answered_by_generator": len(valid_rows),
            "questions_with_generation_failure": len(generation_rows) - len(valid_rows),
            "provider_calls_including_retries": len(attempts),
            "calls_avoided_by_gate": sum(row["call_avoided"] for row in report),
            "full_abstentions_with_provider_call": sum(
                row["abstained"] and row["generation_called"] for row in report
            ),
            "full_abstentions_with_numeric_answer": sum(
                row["abstained"] and row["computation"] is not None for row in report
            ),
        },
        "computation": {
            "scoreable_count": len(scoreable_computations),
            "correct_count": sum(row["computation_correct"] for row in scoreable_computations),
            "accuracy": _safe_divide(
                sum(row["computation_correct"] for row in scoreable_computations),
                len(scoreable_computations),
            ),
        },
        "grounding": {
            "valid_generation_count": len(valid_rows),
            "citation_valid_count": sum(row["citation_valid"] for row in valid_rows),
            "citation_valid_rate": _safe_divide(
                sum(row["citation_valid"] for row in valid_rows), len(valid_rows)
            ),
            "required_evidence_covered_count": sum(
                row["required_evidence_covered"] for row in valid_rows
            ),
            "required_evidence_coverage_rate": _safe_divide(
                sum(row["required_evidence_covered"] for row in valid_rows),
                len(valid_rows),
            ),
            "qualifications_retained_count": sum(
                row["qualifications_retained"] for row in valid_rows
            ),
            "qualifications_retained_rate": _safe_divide(
                sum(row["qualifications_retained"] for row in valid_rows), len(valid_rows)
            ),
            "partial_count": sum(
                row["gate_decision"] == AnswerabilityClassification.PARTIALLY_ANSWERABLE.value
                for row in valid_rows
            ),
            "partial_limitations_retained_count": sum(
                row["partial_limitations_retained"] is True for row in valid_rows
            ),
            "generated_claim_count": claim_count,
            "structurally_unsupported_claim_count": structurally_unsupported_claims,
            "structurally_unsupported_claim_rate": _safe_divide(
                structurally_unsupported_claims, claim_count
            ),
            "unsupported_claim_metric_scope": (
                "Machine-checks exact verified statements, fact IDs, evidence IDs, numeric "
                "and categorical values, and rejects numeric tokens outside the supplied "
                "package. It does not prove all free-text entailment and is not a semantic metric."
            ),
        },
        "failures": [
            {"id": row["id"], "failure": row["failure"]}
            for row in generation_rows
            if row["failure"] is not None
        ],
        "cost_and_latency": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": estimated_cost,
            "pricing_as_of": PRICING_AS_OF,
            "pricing_url": PRICING_URL,
            "mean_latency_ms": statistics.mean(latency_values) if latency_values else None,
            "median_latency_ms": statistics.median(latency_values) if latency_values else None,
            "latency_sample_count": len(latency_values),
        },
        "future_ungated_comparison": {
            "status": "designed_not_run",
            "paired_inputs": "same frozen evaluation split and same verified fact packages",
            "treatment": "current deterministic gate before generator",
            "counterfactual": "generator receives all questions, including full-abstention cases",
            "comparison_metrics": [
                "unsupported-claim rate",
                "full-abstention correctness",
                "provider calls",
                "token cost",
                "latency",
            ],
        },
    }
    return report, summary


def write_generation_report(
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
