import ast
import hashlib
import json
from pathlib import Path

from evaluation.posthoc_metrics import assert_system_c_sanity, derive_metrics


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
ORIGINAL_HASHES = {
    "final_heldout_system_a_report.jsonl": "e789c1d697382569d89c15af6c775dec5be27c12df26cc083cf8a834aac9626d",
    "final_heldout_system_b_report.jsonl": "0045bd996c94cb2852a7a929d4ba62c4f76ee50b5d94647940a62f98dba730ff",
    "final_heldout_system_c_report.jsonl": "8b4790425e9253de8215172e56ca73ace4ed1da06a31f29f625086993e088461",
    "final_heldout_comparison.json": "d9877709f42ac6f3398676d9d7b3864be0bd9b6fde08da9d74d7323cd563c96c",
}
CORRECTED_HASHES = {
    "final_heldout_system_a_metrics_corrected_v1.json": "47d17df7e4856caf4eb2e0c62942bac3cb607a70decabda6d224529fb0a9bc4f",
    "final_heldout_system_b_metrics_corrected_v1.json": "b7d4388022ce0c2e69d7812cb8af4d3e1a61715c510ca7b227935e372db9aef1",
    "final_heldout_system_c_metrics_corrected_v1.json": "5b2842f988a879bb3d739c0afb2574112f677163260f1d7da2149f7f2f7a9e3d",
    "final_heldout_comparison_corrected_v1.json": "570809100027ce0693af4a95395ccf2cc58af7618ee8102c9fcc093722490b3f",
}


def _rows(system_id: str) -> list[dict]:
    path = RESULTS_DIR / f"final_heldout_{system_id}_report.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_deterministic_refusal_text_is_not_a_generated_answer() -> None:
    row = {
        "id": "x",
        "answer": "The corpus cannot answer this request.",
        "generation_called": False,
        "abstained": True,
        "provider_failure": False,
        "failure": None,
        "should_abstain": True,
        "ground_truth_label": "INSUFFICIENT_EVIDENCE",
        "category": "unsupported_causality",
        "computation_correct": None,
        "citation_valid": None,
        "required_evidence_covered": None,
        "partial_limitations_retained": None,
        "qualifications_retained": None,
        "provider_attempts": [],
    }

    metrics = derive_metrics([row], "system_c")

    assert metrics["generated_answers"]["count"] == 0
    assert metrics["abstention"]["correct_count"] == 1
    assert metrics["unsupported_answers"]["count"] == 0
    assert "answered_unsupported_causal_request" not in metrics["errors"]["counts"]


def test_system_c_recomputation_matches_raw_sanity_checks() -> None:
    metrics = derive_metrics(_rows("system_c"), "system_c")

    assert_system_c_sanity(metrics)
    assert metrics["generated_answers"] == {
        "count": 17,
        "coverage": 17 / 45,
        "semantic_rule": (
            "generation_called=true, abstained=false, and no provider failure; "
            "response-string length is ignored"
        ),
    }
    assert metrics["unsupported_answers"]["example_ids"] == ["t029"]
    assert metrics["citations"]["valid_rate"] == 1.0
    assert metrics["required_evidence"]["defined_count"] == 17


def test_system_a_and_b_recomputation_preserves_existing_core_metrics() -> None:
    for system_id in ("system_a", "system_b"):
        metrics = derive_metrics(_rows(system_id), system_id)
        original = json.loads(
            (RESULTS_DIR / f"final_heldout_{system_id}_summary.json").read_text(
                encoding="utf-8"
            )
        )

        assert metrics["generated_answers"]["coverage"] == original["gate"]["coverage"]
        assert metrics["abstention"]["precision"] == original["gate"][
            "abstention_precision"
        ]
        assert metrics["abstention"]["recall"] == original["gate"]["abstention_recall"]
        assert metrics["unsupported_answers"]["rate_on_should_abstain"] == original[
            "critical_safety"
        ]["unsupported_answer_rate_on_should_abstain"]
        assert metrics["end_to_end_success"]["rate"] == original[
            "end_to_end_success_rate"
        ]
        assert metrics["cost_and_latency"]["estimated_cost_usd"] == original[
            "cost_and_latency"
        ]["estimated_cost_usd"]


def test_system_c_genuine_errors_are_retained_without_refusal_hallucinations() -> None:
    metrics = derive_metrics(_rows("system_c"), "system_c")
    counts = metrics["errors"]["counts"]

    assert counts["answered_unsupported_causal_request"] == 1
    assert counts["computation_error"] == 3
    assert counts["failed_partial_answer"] == 6
    assert counts["unnecessary_abstention"] == 9
    for spurious in (
        "accepted_false_premise",
        "missing_period_hallucination",
        "out_of_domain_answer",
        "unavailable_value_hallucination",
    ):
        assert spurious not in counts


def test_correction_code_has_no_provider_or_network_imports() -> None:
    paths = [
        PROJECT_ROOT / "evaluation" / "posthoc_metrics.py",
        PROJECT_ROOT / "scripts" / "recompute_final_metrics.py",
    ]
    forbidden_roots = {
        "app",
        "httpx",
        "openai",
        "requests",
        "sentence_transformers",
        "socket",
        "urllib",
    }
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        roots = {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        roots.update(
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert roots.isdisjoint(forbidden_roots)


def test_original_and_corrected_artifact_hashes_are_stable() -> None:
    for name, expected in ORIGINAL_HASHES.items():
        assert _sha256(RESULTS_DIR / name) == expected
    for name, expected in CORRECTED_HASHES.items():
        assert _sha256(RESULTS_DIR / name) == expected


def test_corrected_comparison_is_derived_only_and_records_no_mutation() -> None:
    comparison = json.loads(
        (RESULTS_DIR / "final_heldout_comparison_corrected_v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert comparison["artifact_type"] == (
        "POST_HOC_METRIC_CORRECTION_FROM_FROZEN_RAW_OUTPUTS"
    )
    assert comparison["predictions_changed"] is False
    assert comparison["systems_changed"] is False
    assert comparison["benchmark_changed"] is False
