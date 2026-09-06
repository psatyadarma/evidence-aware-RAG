"""Create post-hoc metric corrections from frozen raw reports; never run systems."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.posthoc_metrics import (  # noqa: E402
    SHOULD_ABSTAIN,
    assert_system_c_sanity,
    derive_metrics,
)


RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
AUDIT_PATH = PROJECT_ROOT / "evaluation" / "FINAL_HELDOUT_METRIC_CORRECTION.md"
OUTPUTS = {
    "system_a": RESULTS_DIR / "final_heldout_system_a_metrics_corrected_v1.json",
    "system_b": RESULTS_DIR / "final_heldout_system_b_metrics_corrected_v1.json",
    "system_c": RESULTS_DIR / "final_heldout_system_c_metrics_corrected_v1.json",
    "comparison": RESULTS_DIR / "final_heldout_comparison_corrected_v1.json",
}
ORIGINAL_HASHES = {
    "final_heldout_run_started.json": "d7a7fccabb84283f58bd9b9e1c2aa1a4a8d6556d8aaa658d058c1362650a0cf1",
    "final_heldout_system_a_report.jsonl": "e789c1d697382569d89c15af6c775dec5be27c12df26cc083cf8a834aac9626d",
    "final_heldout_system_a_summary.json": "69bd27bc35b6575e9b905dcdd9cd73d417bf5dbb41eb9482939a7da3da531b5c",
    "final_heldout_system_b_report.jsonl": "0045bd996c94cb2852a7a929d4ba62c4f76ee50b5d94647940a62f98dba730ff",
    "final_heldout_system_b_summary.json": "46af121a52086583df805b324b94a20207cf291850f0db0d85d09a948eeb7f02",
    "final_heldout_system_c_report.jsonl": "8b4790425e9253de8215172e56ca73ace4ed1da06a31f29f625086993e088461",
    "final_heldout_system_c_summary.json": "f30d405b492c4ba9e22f03f64821676a675093afbcf44994014052748a7a3a3e",
    "final_heldout_comparison.json": "d9877709f42ac6f3398676d9d7b3864be0bd9b6fde08da9d74d7323cd563c96c",
    "final_heldout_run_completed.json": "6eabe981b7de6dab49635ab4998a5d1fca4168b6719d5a93c9a48e40beed241c",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_originals() -> None:
    for name, expected in ORIGINAL_HASHES.items():
        path = RESULTS_DIR / name
        actual = _sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"original artifact changed: {name}; expected {expected}, got {actual}"
            )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_json(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")


def _close(actual: object, expected: object) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1e-12)
    return actual == expected


def _sanity_check_existing_summary(metrics: dict, summary: dict, system_id: str) -> None:
    comparisons = {
        "coverage": (metrics["generated_answers"]["coverage"], summary["gate"]["coverage"]),
        "abstention_rate": (metrics["abstention"]["rate"], summary["gate"]["abstention_rate"]),
        "abstention_precision": (
            metrics["abstention"]["precision"], summary["gate"]["abstention_precision"]
        ),
        "abstention_recall": (
            metrics["abstention"]["recall"], summary["gate"]["abstention_recall"]
        ),
        "unsupported_rate": (
            metrics["unsupported_answers"]["rate_on_should_abstain"],
            summary["critical_safety"]["unsupported_answer_rate_on_should_abstain"],
        ),
        "correctness": (
            metrics["correctness_among_generated"]["rate"],
            summary["answer_quality"]["answer_correctness_among_answered"],
        ),
        "end_to_end": (
            metrics["end_to_end_success"]["rate"], summary["end_to_end_success_rate"]
        ),
        "calls": (
            metrics["cost_and_latency"]["llm_calls_including_retries"],
            summary["cost_and_latency"]["llm_calls_including_retries"],
        ),
        "cost": (
            metrics["cost_and_latency"]["estimated_cost_usd"],
            summary["cost_and_latency"]["estimated_cost_usd"],
        ),
    }
    disagreements = {
        key: {"recomputed": actual, "original": expected}
        for key, (actual, expected) in comparisons.items()
        if not _close(actual, expected)
    }
    if disagreements:
        raise ValueError(f"{system_id} summary sanity check failed: {disagreements}")


def _normalise_development_c(rows: list[dict]) -> list[dict]:
    normalised = []
    for source in rows:
        row = dict(source)
        row["should_abstain"] = row["ground_truth_label"] in SHOULD_ABSTAIN
        row["provider_failure"] = row.get("failure") is not None
        row.setdefault("category", "unknown")
        normalised.append(row)
    return normalised


def _generalization_gap(heldout: dict[str, dict]) -> dict:
    development = {
        "system_a": derive_metrics(
            _read_jsonl(RESULTS_DIR / "development_system_a_v1_report.jsonl"), "system_a"
        ),
        "system_b": derive_metrics(
            _read_jsonl(RESULTS_DIR / "development_system_b_v1_report.jsonl"), "system_b"
        ),
        "system_c": derive_metrics(
            _normalise_development_c(
                _read_jsonl(
                    RESULTS_DIR / "development_grounded_generation_v2_report.jsonl"
                )
            ),
            "system_c",
        ),
    }
    fields = {
        "generated_answer_coverage": ("generated_answers", "coverage"),
        "correctness_among_generated": ("correctness_among_generated", "rate"),
        "abstention_recall": ("abstention", "recall"),
        "unsupported_answer_rate": (
            "unsupported_answers", "rate_on_should_abstain"
        ),
        "partial_success_rate": ("partial_answers", "success_rate"),
        "end_to_end_success": ("end_to_end_success", "rate"),
    }
    result = {}
    for system_id in ("system_a", "system_b", "system_c"):
        result[system_id] = {}
        for metric, path in fields.items():
            dev_value = development[system_id][path[0]][path[1]]
            test_value = heldout[system_id][path[0]][path[1]]
            result[system_id][metric] = {
                "development": dev_value,
                "heldout": test_value,
                "heldout_minus_development": test_value - dev_value,
            }
    return result


def _comparison(metrics: dict[str, dict], gaps: dict) -> dict:
    rows = []
    for system_id in ("system_a", "system_b", "system_c"):
        item = metrics[system_id]
        rows.append(
            {
                "system_id": system_id,
                "end_to_end_success": item["end_to_end_success"],
                "generated_answer_coverage": item["generated_answers"],
                "abstention": item["abstention"],
                "unsupported_answers": item["unsupported_answers"],
                "correctness_among_generated": item["correctness_among_generated"],
                "partial_answers": item["partial_answers"],
                "citations": item["citations"],
                "required_evidence": item["required_evidence"],
                "source_qualifications": item["source_qualifications"],
                "provider_failures": item["provider_failures"],
                "cost_and_latency": item["cost_and_latency"],
            }
        )
    return {
        "schema_version": 1,
        "evaluation_split": "heldout",
        "artifact_type": "POST_HOC_METRIC_CORRECTION_FROM_FROZEN_RAW_OUTPUTS",
        "one_shot_inference_run": "original Checkpoint 12 run; not rerun",
        "predictions_changed": False,
        "systems_changed": False,
        "benchmark_changed": False,
        "metric_correction": (
            "System C deterministic refusal text is not a generated/substantive answer; "
            "explicit generation_called and abstained fields control evaluation semantics."
        ),
        "systems": rows,
        "development_to_heldout": gaps,
        "original_result_hashes": ORIGINAL_HASHES,
    }


def _audit_markdown(
    discovered_at: str,
    metrics: dict[str, dict],
    corrected_hashes: dict[str, str],
    gaps: dict,
) -> str:
    c = metrics["system_c"]
    original_lines = "\n".join(
        f"- `{name}`: `{digest}`" for name, digest in ORIGINAL_HASHES.items()
    )
    corrected_lines = "\n".join(
        f"- `{name}`: `{digest}`" for name, digest in corrected_hashes.items()
    )
    gap_lines = "\n".join(
        f"- {system_id}: "
        + ", ".join(
            f"{metric} {values['development']:.4f} → {values['heldout']:.4f} "
            f"(Δ {values['heldout_minus_development']:+.4f})"
            for metric, values in system_gaps.items()
        )
        for system_id, system_gaps in gaps.items()
    )
    return f"""# Final Held-out Metric Correction Audit

Discovered and corrected at: `{discovered_at}`.

## Status and scope

This is a post-hoc **evaluation-only** correction to derived Checkpoint 12 metrics. The original one-shot inference run, raw reports, summaries, comparison, model outputs, gates, systems, prompts, threshold, corpus, and benchmark remain unchanged.

## Exact bug

The original System C evaluator set `substantive_answer` from non-empty response text. Deterministic abstentions intentionally contain explanatory refusal text, so all 28 refusals were incorrectly treated as substantive answers. Correct semantics use explicit execution/action fields: a generated answer requires `generation_called=true` with no abstention or provider failure; abstention uses `abstained=true`. String length is never a routing signal.

## Affected metrics

System C generated-answer count/coverage, correctness denominator/rate, citation denominator/rate, required-evidence denominator/rate, unsupported-answer count/rate, and error categorization were affected. Corrected System C values are: generated `{c['generated_answers']['count']}/45`, unsupported `{c['unsupported_answers']['count']}/20`, computation correctness `{c['correctness_among_generated']['correct_count']}/{c['correctness_among_generated']['generated_count']}`, citations `{c['citations']['valid_count']}/{c['citations']['defined_count']}`, and required evidence `{c['required_evidence']['covered_count']}/{c['required_evidence']['defined_count']}`.

## Metrics not affected

Raw generation calls, deterministic abstention count/rate, correct-abstention count, abstention precision/recall, correct-computation count, partial-success count, qualification-retention count, provider failures, calls, token usage, cost, generation latency, and the narrow machine-checkable end-to-end success count were already derived from explicit fields and remain unchanged. System A and B sanity checks reproduce their original summaries.

## No new inference or provider activity

The correction command reads existing JSON/JSONL files and uses Python standard-library arithmetic plus `evaluation/posthoc_metrics.py`. It does not import application pipelines, provider adapters, HTTP clients, or model SDKs; it does not access API credentials. No held-out question was rerun and no new provider attempt was created. Tests enforce this import boundary.

## Genuine errors retained

- System C actual unsupported answer: `{', '.join(c['unsupported_answers']['example_ids'])}`.
- System C unnecessary abstentions: `{', '.join(c['errors']['unnecessary_abstention_ids'])}`.
- Corrected System C error counts: `{json.dumps(c['errors']['counts'], sort_keys=True)}`.
- Correct deterministic refusals are no longer labelled as causal, temporal, unavailable-value, false-premise, or out-of-domain hallucinations.

## Development-to-held-out observations

{gap_lines}

These are descriptive gaps only. No system, prompt, threshold, parser, retrieval rule, benchmark item, or prediction was changed in response.

## Original artifact SHA-256 values

{original_lines}

The correction script verifies these values both before and after writing derived artifacts.

## Corrected derived artifact SHA-256 values

{corrected_lines}
"""


def main() -> None:
    targets = [*OUTPUTS.values(), AUDIT_PATH]
    existing = [str(path) for path in targets if path.exists()]
    if existing:
        raise SystemExit("Corrected artifact exists; refusing overwrite: " + ", ".join(existing))
    _verify_originals()

    rows = {
        system_id: _read_jsonl(
            RESULTS_DIR / f"final_heldout_{system_id}_report.jsonl"
        )
        for system_id in ("system_a", "system_b", "system_c")
    }
    metrics = {
        system_id: derive_metrics(system_rows, system_id)
        for system_id, system_rows in rows.items()
    }
    assert_system_c_sanity(metrics["system_c"])
    for system_id in ("system_a", "system_b"):
        _sanity_check_existing_summary(
            metrics[system_id],
            _read_json(RESULTS_DIR / f"final_heldout_{system_id}_summary.json"),
            system_id,
        )
    gaps = _generalization_gap(metrics)
    comparison = _comparison(metrics, gaps)

    for system_id in ("system_a", "system_b", "system_c"):
        payload = {
            "schema_version": 1,
            "artifact_type": "POST_HOC_METRIC_CORRECTION_FROM_FROZEN_RAW_OUTPUTS",
            "source_report": f"evaluation/results/final_heldout_{system_id}_report.jsonl",
            "source_report_sha256": ORIGINAL_HASHES[
                f"final_heldout_{system_id}_report.jsonl"
            ],
            "raw_predictions_changed": False,
            "metrics": metrics[system_id],
        }
        _write_json(OUTPUTS[system_id], payload)
    _write_json(OUTPUTS["comparison"], comparison)
    corrected_hashes = {
        str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"): _sha256(path)
        for path in OUTPUTS.values()
    }
    discovered_at = datetime.now(timezone.utc).isoformat()
    with AUDIT_PATH.open("x", encoding="utf-8", newline="\n") as output:
        output.write(_audit_markdown(discovered_at, metrics, corrected_hashes, gaps))

    _verify_originals()
    print(
        json.dumps(
            {
                "status": "post-hoc metrics corrected",
                "provider_calls": 0,
                "originals_unchanged": True,
                "outputs": corrected_hashes,
                "audit": str(AUDIT_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
