"""Create the one-time machine-readable Checkpoint 11 protocol freeze."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.baseline_generation import PROMPT_HASH as BASELINE_PROMPT_HASH  # noqa: E402
from app.baseline_generation import PROMPT_VERSION as BASELINE_PROMPT_VERSION  # noqa: E402
from app.generation import PROMPT_HASH as SYSTEM_C_PROMPT_HASH  # noqa: E402
from app.generation import PROMPT_VERSION as SYSTEM_C_PROMPT_VERSION  # noqa: E402
from app.semantic_retrieval import (  # noqa: E402
    MODEL_DIMENSION,
    MODEL_ID,
    MODEL_REVISION,
    QUERY_PREFIX,
)
from evaluation.final_protocol import sha256_file, write_immutable_json  # noqa: E402


OUTPUT = PROJECT_ROOT / "evaluation" / "final_evaluation_protocol.freeze.json"
CHECKSUM_OUTPUT = PROJECT_ROOT / "evaluation" / "final_evaluation_protocol.sha256"
THRESHOLD_ARTIFACT = (
    PROJECT_ROOT / "evaluation" / "results" / "development_threshold_selection.json"
)
HELDOUT_PATH = PROJECT_ROOT / "evaluation" / "heldout_benchmark.jsonl"


FROZEN_FILES = [
    "app/answerability.py",
    "app/baseline_generation.py",
    "app/comparison_systems.py",
    "app/computation.py",
    "app/evidence.py",
    "app/generation.py",
    "app/ingestion.py",
    "app/llm_answerability.py",
    "app/llm_answerability_v2.py",
    "app/models.py",
    "app/pipeline.py",
    "app/providers/openai_responses.py",
    "app/retrieval.py",
    "app/semantic_retrieval.py",
    "app/sources.py",
    "app/structured_retrieval.py",
    "app/threshold_gate.py",
    "data/raw/manifest.json",
    "data/raw/population_indicators_annual.json",
    "data/raw/resident_population_census_2020.json",
    "data/raw/residents_by_planning_region_annual.json",
    "data/processed/population_indicators_annual.jsonl",
    "data/processed/resident_population_census_2020.jsonl",
    "data/processed/residents_by_planning_region_annual.jsonl",
    "data/embeddings/bge-small-en-v1.5/embeddings.npy",
    "data/embeddings/bge-small-en-v1.5/evidence_ids.json",
    "data/embeddings/bge-small-en-v1.5/metadata.json",
    "evaluation/benchmark.jsonl",
    "evaluation/benchmark.py",
    "evaluation/final_protocol.py",
    "evaluation/generation_evaluation.py",
    "evaluation/system_comparison.py",
    "evaluation/threshold_selection.py",
    "evaluation/heldout_benchmark.freeze.json",
    "evaluation/FINAL_EVALUATION_PROTOCOL.md",
    "evaluation/results/development_answerability_report.jsonl",
    "evaluation/results/development_answerability_summary.json",
    "evaluation/results/development_grounded_generation_v1_error_analysis.json",
    "evaluation/results/development_grounded_generation_v1_report.jsonl",
    "evaluation/results/development_grounded_generation_v1_summary.json",
    "evaluation/results/development_grounded_generation_v2_claim_audit.json",
    "evaluation/results/development_grounded_generation_v2_report.jsonl",
    "evaluation/results/development_grounded_generation_v2_summary.json",
    "evaluation/results/development_llm_answerability_report.jsonl",
    "evaluation/results/development_llm_answerability_summary.json",
    "evaluation/results/development_llm_answerability_v2_error_analysis.json",
    "evaluation/results/development_llm_answerability_v2_report.jsonl",
    "evaluation/results/development_llm_answerability_v2_summary.json",
    "evaluation/results/development_threshold_selection.json",
    "evaluation/results/development_system_a_v1_report.jsonl",
    "evaluation/results/development_system_a_v1_summary.json",
    "evaluation/results/development_system_b_v1_report.jsonl",
    "evaluation/results/development_system_b_v1_summary.json",
    "evaluation/results/development_baseline_claim_audit_v1.json",
    "evaluation/results/development_system_comparison_final.json",
    "evaluation/results/lexical_retrieval_report.jsonl",
    "evaluation/results/lexical_retrieval_summary.json",
    "evaluation/results/semantic_retrieval_report.jsonl",
    "evaluation/results/semantic_retrieval_summary.json",
    "evaluation/results/structured_retrieval_report.jsonl",
    "evaluation/results/structured_retrieval_summary.json",
    "evaluation/results/retrieval_comparison.json",
    "evaluation/results/three_way_retrieval_comparison.json",
    "scripts/evaluate_final_heldout.py",
    "scripts/freeze_final_protocol.py",
]


def main() -> None:
    if OUTPUT.exists() or CHECKSUM_OUTPUT.exists():
        raise SystemExit("Final protocol artifact exists; refusing to overwrite it.")
    missing = [item for item in FROZEN_FILES if not (PROJECT_ROOT / item).is_file()]
    if missing:
        raise SystemExit("Cannot freeze missing artifacts: " + ", ".join(missing))
    threshold = json.loads(THRESHOLD_ARTIFACT.read_text(encoding="utf-8"))
    heldout_sha256 = sha256_file(HELDOUT_PATH)
    expected_heldout = "99eb8be69c33d1744ae15c8891bec5f7e963142e334172753b4fe39361947065"
    if heldout_sha256 != expected_heldout:
        raise SystemExit(
            f"Held-out checksum changed: expected {expected_heldout}, got {heldout_sha256}"
        )
    payload = {
        "schema_version": 1,
        "status": "FROZEN_NOT_RUN",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_question": (
            "Is semantic relevance alone sufficient to decide whether a RAG system should "
            "answer, or does explicit evidence-sufficiency reasoning provide safer and more "
            "reliable behavior?"
        ),
        "explicit_statement": (
            "The 45-question held-out benchmark has not yet been evaluated under this protocol."
        ),
        "heldout": {
            "path": "evaluation/heldout_benchmark.jsonl",
            "sha256": heldout_sha256,
            "example_count": 45,
            "evaluated": False,
        },
        "model": {
            "provider": "openai_responses_api",
            "snapshot": "gpt-4.1-mini-2025-04-14",
            "temperature": 0.0,
            "max_output_tokens": 1000,
            "max_attempts": 2,
            "pricing_as_of": "2026-09-05",
            "standard_input_usd_per_million": 0.40,
            "cached_input_usd_per_million": 0.10,
            "output_usd_per_million": 1.60,
            "pricing_url": "https://developers.openai.com/api/docs/models/gpt-4.1-mini",
        },
        "shared_retrieval": {
            "implementation": "frozen deterministic structured retriever",
            "answer_evidence_identical_across_systems": True,
        },
        "system_a": {
            "id": "system_a_ungated_structured_rag_baseline_v1",
            "flow": "question -> structured retrieval -> baseline generation",
            "application_gate": "none",
            "model_may_abstain": True,
            "prompt_version": BASELINE_PROMPT_VERSION,
            "prompt_sha256": BASELINE_PROMPT_HASH,
        },
        "system_b": {
            "id": "system_b_max_bge_threshold_structured_rag_baseline_v1",
            "flow": (
                "question -> structured retrieval -> maximum BGE cosine threshold -> "
                "shared baseline generation"
            ),
            "threshold": threshold["selected_threshold"],
            "comparison": "score >= threshold",
            "zero_evidence": "score NONE; deterministic abstention; no generation call",
            "prompt_version": BASELINE_PROMPT_VERSION,
            "prompt_sha256": BASELINE_PROMPT_HASH,
            "threshold_abstention_response": "generic; contains no deterministic diagnostics",
        },
        "system_c": {
            "id": "system_c_checkpoint_10_deterministic_evidence_aware_rag_v2",
            "flow": (
                "question -> structured interpretation/retrieval -> deterministic "
                "answerability -> deterministic computation -> grounded generation v2"
            ),
            "prompt_version": SYSTEM_C_PROMPT_VERSION,
            "prompt_sha256": SYSTEM_C_PROMPT_HASH,
            "full_abstention_calls_generation": False,
        },
        "bge": {
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "dimension": MODEL_DIMENSION,
            "query_prefix": QUERY_PREFIX,
            "document_prefix": "",
            "normalized": True,
            "dtype": "float32",
            "threshold_score_formula": (
                "max cosine(normalized query embedding, normalized embedding of each "
                "structured-retrieved evidence observation)"
            ),
        },
        "threshold_selection": {
            "split": "development_only_25_questions",
            "label_mapping": {
                "ALLOW": ["ANSWERABLE", "PARTIALLY_ANSWERABLE"],
                "ABSTAIN": ["INSUFFICIENT_EVIDENCE", "OUT_OF_SCOPE"],
            },
            "candidate_generation": (
                "nextafter below finite minimum, every unique finite observed score, "
                "nextafter above finite maximum; NONE always abstains"
            ),
            "objective": threshold["objective"],
            "tie_break_order": threshold["tie_break_order"],
            "selected_threshold": threshold["selected_threshold"],
            "selected_binary_macro_f1": threshold["selected_binary_macro_f1"],
            "selected_abstention_recall": threshold["selected_abstention_recall"],
            "score_distribution": threshold["score_distribution"],
            "candidate_artifact": "evaluation/results/development_threshold_selection.json",
            "candidate_artifact_sha256": sha256_file(THRESHOLD_ARTIFACT),
        },
        "metrics": {
            "full_abstention_labels": ["INSUFFICIENT_EVIDENCE", "OUT_OF_SCOPE"],
            "coverage": "substantive answers / all examples",
            "abstention_rate": "valid abstentions / all examples",
            "abstention_precision": "correct full abstentions / valid abstentions",
            "abstention_recall": "correct full abstentions / should-abstain examples",
            "unsupported_answer_rate_on_should_abstain": (
                "substantive answers to the unsupported requested claim / all examples "
                "labelled INSUFFICIENT_EVIDENCE or OUT_OF_SCOPE"
            ),
            "end_to_end_success": (
                "correct supported answers plus correct abstentions / all examples; "
                "provider failures are failures"
            ),
            "answer_quality": [
                "numeric/computation correctness",
                "categorical/comparison correctness where typed",
                "citation-ID validity",
                "required-evidence coverage",
                "unsupported factual claims where machine-checkable",
                "partial-answer success",
            ],
            "partial_success": (
                "supported portion answered, unsupported portion not falsely answered, "
                "and limitation explicitly conveyed"
            ),
            "cost_latency": [
                "LLM calls including retries",
                "calls avoided",
                "input/cached-input/output tokens",
                "estimated USD",
                "mean/median generation latency",
                "gate latency where instrumented",
            ],
            "gate_only_reported_separately": True,
            "automatic_semantic_score": False,
        },
        "provider_failure_policy": {
            "separate_state": True,
            "counts_as_correct_abstention": False,
            "counts_as_incorrect_factual_answer": False,
            "counts_as_end_to_end_success": False,
            "attempts_and_failures_retained": True,
        },
        "determinism": {
            "random_seed": 0,
            "temperature": 0.0,
            "bge_device": "cpu",
            "bge_local_files_only": True,
            "structured_retrieval": "deterministic",
            "threshold_comparison": "inclusive_greater_than_or_equal",
            "manual_prediction_corrections": 0,
        },
        "comparison_scope": {
            "complete_policies": True,
            "single_independent_variable_claim": False,
            "gate_level_comparison_also_reported": True,
        },
        "one_shot_policy": {
            "command": "python scripts/evaluate_final_heldout.py --live",
            "ordinary_overwrite_allowed": False,
            "post_start_changes_allowed": [],
            "infrastructure_failure": (
                "Preserve start marker and partial outputs; document cause before a "
                "separately versioned exceptional rerun is considered."
            ),
        },
        "frozen_artifacts": {
            item: sha256_file(PROJECT_ROOT / item) for item in sorted(FROZEN_FILES)
        },
    }
    write_immutable_json(OUTPUT, payload)
    protocol_sha256 = sha256_file(OUTPUT)
    with CHECKSUM_OUTPUT.open("x", encoding="ascii", newline="\n") as output:
        output.write(f"{protocol_sha256}  evaluation/final_evaluation_protocol.freeze.json\n")
    print(json.dumps({"protocol": str(OUTPUT), "sha256": protocol_sha256}, indent=2))


if __name__ == "__main__":
    main()
