"""Execute the preregistered one-shot held-out comparison (DO NOT run casually)."""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sentence_transformers import SentenceTransformer  # noqa: E402

from app.answerability import DeterministicAnswerabilityClassifier  # noqa: E402
from app.baseline_generation import BaselineAnswerGenerator  # noqa: E402
from app.comparison_systems import ThresholdRagSystem, UngatedRagSystem  # noqa: E402
from app.computation import DeterministicComputationEngine  # noqa: E402
from app.config import Settings  # noqa: E402
from app.generation import GroundedAnswerGenerator  # noqa: E402
from app.pipeline import EvidenceAwarePipeline  # noqa: E402
from app.providers.openai_responses import OpenAIResponsesProvider  # noqa: E402
from app.retrieval import build_retrieval_units  # noqa: E402
from app.semantic_retrieval import (  # noqa: E402
    MODEL_DIMENSION,
    MODEL_ID,
    MODEL_REVISION,
    load_embedding_artifact,
)
from app.structured_retrieval import StructuredRetriever  # noqa: E402
from app.threshold_gate import StructuredEvidenceSimilarityScorer  # noqa: E402
from evaluation.benchmark import load_benchmark, load_evidence_index  # noqa: E402
from evaluation.final_protocol import (  # noqa: E402
    load_and_validate_protocol,
    read_protocol_checksum,
    refuse_existing_outputs,
    validate_frozen_artifacts,
    validate_heldout_checksum,
    write_immutable_json,
    write_immutable_jsonl,
)
from evaluation.system_comparison import (  # noqa: E402
    evaluate_baseline_system,
    evaluate_selected_system,
)


PROTOCOL_PATH = PROJECT_ROOT / "evaluation" / "final_evaluation_protocol.freeze.json"
PROTOCOL_CHECKSUM_PATH = PROJECT_ROOT / "evaluation" / "final_evaluation_protocol.sha256"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
EMBEDDING_DIR = PROJECT_ROOT / "data" / "embeddings" / "bge-small-en-v1.5"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"


def _output_paths() -> dict[str, Path]:
    return {
        "started": RESULTS_DIR / "final_heldout_run_started.json",
        "system_a_report": RESULTS_DIR / "final_heldout_system_a_report.jsonl",
        "system_a_summary": RESULTS_DIR / "final_heldout_system_a_summary.json",
        "system_b_report": RESULTS_DIR / "final_heldout_system_b_report.jsonl",
        "system_b_summary": RESULTS_DIR / "final_heldout_system_b_summary.json",
        "system_c_report": RESULTS_DIR / "final_heldout_system_c_report.jsonl",
        "system_c_summary": RESULTS_DIR / "final_heldout_system_c_summary.json",
        "comparison": RESULTS_DIR / "final_heldout_comparison.json",
        "completed": RESULTS_DIR / "final_heldout_run_completed.json",
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Required acknowledgement of paid calls and the one-shot held-out run.",
    )
    return parser.parse_args()


def _write_system_outputs(
    report_path: Path,
    summary_path: Path,
    report: list[dict],
    summary: dict,
) -> None:
    write_immutable_jsonl(report_path, report)
    write_immutable_json(summary_path, summary)


def _comparison(summaries: dict[str, dict], threshold: float) -> dict:
    selective_risk = []
    for system_id, summary in summaries.items():
        selective_risk.append(
            {
                "system": system_id,
                "coverage": summary["gate"]["coverage"],
                "answer_correctness_among_answered": summary["answer_quality"][
                    "answer_correctness_among_answered"
                ],
                "abstention_recall": summary["gate"]["abstention_recall"],
                "unsupported_answer_rate_on_should_abstain": summary[
                    "critical_safety"
                ]["unsupported_answer_rate_on_should_abstain"],
                "provider_failures": summary["gate"]["provider_failures"],
            }
        )
    return {
        "evaluation_split": "heldout",
        "protocol_status_at_start": "FROZEN_NOT_RUN",
        "threshold": threshold,
        "complete_policy_comparison": summaries,
        "gate_only_comparison": {
            key: value["application_gate_only"] for key, value in summaries.items()
        },
        "selective_risk": selective_risk,
        "interpretation": (
            "This is a complete-policy comparison. System C additionally includes frozen "
            "deterministic computation and evidence-aware generation. The gate-only table "
            "separately compares ungated, scalar semantic-threshold, and explicit "
            "deterministic-sufficiency decisions."
        ),
        "manual_prediction_corrections": 0,
    }


def main() -> None:
    args = _arguments()
    if not args.live:
        raise SystemExit(
            "Refusing the one-shot held-out evaluation without explicit --live acknowledgement."
        )

    paths = _output_paths()
    refuse_existing_outputs(paths.values())
    protocol = load_and_validate_protocol(PROTOCOL_PATH, PROTOCOL_CHECKSUM_PATH)
    validate_frozen_artifacts(PROJECT_ROOT, protocol["frozen_artifacts"])
    heldout = protocol["heldout"]
    actual_heldout_hash = validate_heldout_checksum(
        PROJECT_ROOT, heldout["path"], heldout["sha256"]
    )

    settings = Settings()
    model = protocol["model"]
    if settings.model_provider.casefold() != "openai":
        raise SystemExit("Final protocol requires the OpenAI provider.")
    if settings.model_name != model["snapshot"]:
        raise SystemExit(f"Final protocol requires {model['snapshot']}.")
    if settings.model_max_attempts != model["max_attempts"]:
        raise SystemExit("RAG_MODEL_MAX_ATTEMPTS differs from the frozen protocol.")
    if settings.model_max_output_tokens != model["max_output_tokens"]:
        raise SystemExit("RAG_MODEL_MAX_OUTPUT_TOKENS differs from the frozen protocol.")
    if settings.model_api_key is None or not settings.model_api_key.get_secret_value():
        raise SystemExit("RAG_MODEL_API_KEY is required for the final live run.")

    random.seed(protocol["determinism"]["random_seed"])
    np.random.seed(protocol["determinism"]["random_seed"])
    threshold = float(protocol["system_b"]["threshold"])
    run_started_at = datetime.now(timezone.utc).isoformat()
    write_immutable_json(
        paths["started"],
        {
            "status": "RUNNING",
            "started_at_utc": run_started_at,
            "protocol_sha256": read_protocol_checksum(PROTOCOL_CHECKSUM_PATH),
            "heldout_sha256_validated": actual_heldout_hash,
            "one_shot": True,
        },
    )

    # This is the only final-evaluation entry point that loads the held-out records.
    benchmark_path = PROJECT_ROOT / heldout["path"]
    examples = load_benchmark(benchmark_path, load_evidence_index(PROCESSED_DIR))
    if len(examples) != heldout["example_count"]:
        raise RuntimeError(
            f"held-out count changed: expected {heldout['example_count']}, got {len(examples)}"
        )

    units = build_retrieval_units(PROCESSED_DIR)
    retriever = StructuredRetriever(units)
    _, embeddings = load_embedding_artifact(
        units,
        EMBEDDING_DIR,
        expected_model_id=MODEL_ID,
        expected_model_revision=MODEL_REVISION,
        expected_dimension=MODEL_DIMENSION,
    )
    encoder = SentenceTransformer(
        MODEL_ID, revision=MODEL_REVISION, device="cpu", local_files_only=True
    )
    scorer = StructuredEvidenceSimilarityScorer(units, embeddings, encoder)
    provider = OpenAIResponsesProvider(
        api_key=settings.model_api_key.get_secret_value(),
        model=settings.model_name,
        timeout_seconds=settings.model_timeout_seconds,
    )
    baseline_generator = BaselineAnswerGenerator(
        provider,
        max_attempts=settings.model_max_attempts,
        max_output_tokens=settings.model_max_output_tokens,
    )
    system_a = UngatedRagSystem(retriever, baseline_generator)
    system_b = ThresholdRagSystem(
        retriever, scorer, baseline_generator, threshold
    )
    system_c = EvidenceAwarePipeline(
        DeterministicAnswerabilityClassifier(retriever, units),
        DeterministicComputationEngine(units),
        GroundedAnswerGenerator(
            provider,
            max_attempts=settings.model_max_attempts,
            max_output_tokens=settings.model_max_output_tokens,
        ),
        units,
    )

    report_a, summary_a = evaluate_baseline_system(examples, system_a)
    summary_a["evaluation_split"] = "heldout"
    _write_system_outputs(
        paths["system_a_report"], paths["system_a_summary"], report_a, summary_a
    )

    report_b, summary_b = evaluate_baseline_system(examples, system_b)
    summary_b["evaluation_split"] = "heldout"
    _write_system_outputs(
        paths["system_b_report"], paths["system_b_summary"], report_b, summary_b
    )

    report_c, summary_c = evaluate_selected_system(examples, system_c)
    summary_c["evaluation_split"] = "heldout"
    _write_system_outputs(
        paths["system_c_report"], paths["system_c_summary"], report_c, summary_c
    )

    summaries = {
        "system_a": summary_a,
        "system_b": summary_b,
        "system_c": summary_c,
    }
    comparison = _comparison(summaries, threshold)
    write_immutable_json(paths["comparison"], comparison)
    write_immutable_json(
        paths["completed"],
        {
            "status": "COMPLETED",
            "started_at_utc": run_started_at,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "protocol_sha256": read_protocol_checksum(PROTOCOL_CHECKSUM_PATH),
            "heldout_sha256_validated": actual_heldout_hash,
            "output_files": [
                str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
                for key, path in paths.items()
                if key not in {"started", "completed"}
            ],
        },
    )
    print(json.dumps(comparison, indent=2, sort_keys=True))
    print("Final held-out evaluation completed. Results are immutable.")


if __name__ == "__main__":
    main()
