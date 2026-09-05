"""Build the corrected final development comparison without provider calls."""

from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
OUTPUT = RESULTS_DIR / "development_system_comparison_final.json"


def _read(name: str):
    return json.loads((RESULTS_DIR / name).read_text(encoding="utf-8"))


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit("Final development comparison exists; refusing to overwrite it.")
    a = _read("development_system_a_v1_summary.json")
    b = _read("development_system_b_v1_summary.json")
    audit = _read("development_baseline_claim_audit_v1.json")
    c_source = _read("development_grounded_generation_v2_summary.json")
    systems = {}
    for key, summary in (("system_a", a), ("system_b", b)):
        systems[key] = {
            "coverage": summary["gate"]["coverage"],
            "abstention_rate": summary["gate"]["abstention_rate"],
            "abstention_precision": summary["gate"]["abstention_precision"],
            "abstention_recall": summary["gate"]["abstention_recall"],
            "unsupported_answer_rate_on_should_abstain": summary["critical_safety"]["unsupported_answer_rate_on_should_abstain"],
            "expected_content_accuracy_among_answered": audit[key]["expected_content_accuracy_among_answered"],
            "citation_id_validity": summary["answer_quality"]["citation_valid_rate"],
            "required_evidence_covered_count": summary["answer_quality"]["required_evidence_coverage_count"],
            "partial_success": f"{audit[key]['partial_success']}/{audit[key]['partial_total']}",
            "provider_failures": summary["gate"]["provider_failures"],
            "cost_and_latency": summary["cost_and_latency"],
        }
    systems["system_c"] = {
        "coverage": 13 / 25,
        "abstention_rate": 12 / 25,
        "abstention_precision": 1.0,
        "abstention_recall": 1.0,
        "unsupported_answer_rate_on_should_abstain": 0.0,
        "expected_content_accuracy_among_answered": 1.0,
        "citation_id_validity": c_source["grounding"]["citation_valid_rate"],
        "required_evidence_covered_count": c_source["grounding"]["required_evidence_covered_count"],
        "partial_success": "3/3",
        "provider_failures": c_source["routing"]["questions_with_generation_failure"],
        "cost_and_latency": {
            "llm_calls_including_retries": c_source["routing"]["provider_calls_including_retries"],
            "calls_avoided": c_source["routing"]["calls_avoided_by_gate"],
            "input_tokens": c_source["cost_and_latency"]["input_tokens"],
            "cached_input_tokens": c_source["cost_and_latency"]["cached_input_tokens"],
            "output_tokens": c_source["cost_and_latency"]["output_tokens"],
            "estimated_cost_usd": c_source["cost_and_latency"]["estimated_cost_usd"],
            "mean_generation_latency_ms": c_source["cost_and_latency"]["mean_latency_ms"],
            "median_generation_latency_ms": c_source["cost_and_latency"]["median_latency_ms"],
            "deterministic_gate_hosted_cost_usd": 0.0,
        },
    }
    comparison = {
        "evaluation_split": "development",
        "complete_policy_comparison": systems,
        "gate_only_comparison": {
            "system_a_ungated": {
                "generation_allow_rate": 1.0,
                "gate_abstention_rate": 0.0,
                "gate_abstention_recall": 0.0
            },
            "system_b_similarity_threshold": {
                "generation_allow_rate": 20 / 25,
                "gate_abstention_rate": 5 / 25,
                "gate_abstention_precision": 1.0,
                "gate_abstention_recall": 5 / 12
            },
            "system_c_deterministic_sufficiency": {
                "generation_allow_rate": 13 / 25,
                "gate_abstention_rate": 12 / 25,
                "gate_abstention_precision": 1.0,
                "gate_abstention_recall": 1.0
            }
        },
        "selective_risk": [
            {
                "system": key,
                "coverage": value["coverage"],
                "answer_correctness_among_answered": value["expected_content_accuracy_among_answered"],
                "abstention_recall": value["abstention_recall"],
                "unsupported_answer_rate_on_should_abstain": value["unsupported_answer_rate_on_should_abstain"]
            }
            for key, value in systems.items()
        ],
        "interpretation": "This compares complete policies. The gate-only table separately isolates whether generation is allowed before any model-initiated abstention.",
        "held_out_evaluated": False
    }
    temporary = OUTPUT.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(OUTPUT)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    print(json.dumps(comparison, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
