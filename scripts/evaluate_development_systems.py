"""Run the authorized live development comparison for baseline Systems A and B."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sentence_transformers import SentenceTransformer  # noqa: E402

from app.baseline_generation import BaselineAnswerGenerator  # noqa: E402
from app.comparison_systems import ThresholdRagSystem, UngatedRagSystem  # noqa: E402
from app.config import Settings  # noqa: E402
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
from evaluation.system_comparison import (  # noqa: E402
    evaluate_baseline_system,
    system_c_comparable_summary,
    write_report,
)


PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
EMBEDDING_DIR = PROJECT_ROOT / "data" / "embeddings" / "bge-small-en-v1.5"
DEVELOPMENT_BENCHMARK = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"
THRESHOLD_PATH = PROJECT_ROOT / "evaluation" / "results" / "development_threshold_selection.json"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
SYSTEM_C_REPORT = RESULTS_DIR / "development_grounded_generation_v2_report.jsonl"
SYSTEM_C_SUMMARY = RESULTS_DIR / "development_grounded_generation_v2_summary.json"
VERSION = "v1"


def _paths(system: str):
    return (
        RESULTS_DIR / f"development_{system}_{VERSION}_report.jsonl",
        RESULTS_DIR / f"development_{system}_{VERSION}_summary.json",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("Refusing paid baseline calls without explicit --live acknowledgement.")
    targets = [*_paths("system_a"), *_paths("system_b"), RESULTS_DIR / f"development_system_comparison_{VERSION}.json"]
    if any(path.exists() for path in targets):
        raise SystemExit("Development comparison artifact exists; refusing to overwrite it.")
    settings = Settings()
    if settings.model_name != "gpt-4.1-mini-2025-04-14":
        raise SystemExit("Comparison requires gpt-4.1-mini-2025-04-14.")
    if settings.model_api_key is None or not settings.model_api_key.get_secret_value():
        raise SystemExit("RAG_MODEL_API_KEY is required.")
    threshold_payload = json.loads(THRESHOLD_PATH.read_text(encoding="utf-8"))
    threshold = float(threshold_payload["selected_threshold"])
    units = build_retrieval_units(PROCESSED_DIR)
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
    retriever = StructuredRetriever(units)
    scorer = StructuredEvidenceSimilarityScorer(units, embeddings, encoder)
    provider = OpenAIResponsesProvider(
        api_key=settings.model_api_key.get_secret_value(),
        model=settings.model_name,
        timeout_seconds=settings.model_timeout_seconds,
    )
    examples = load_benchmark(
        DEVELOPMENT_BENCHMARK, load_evidence_index(PROCESSED_DIR)
    )
    system_a = UngatedRagSystem(
        retriever,
        BaselineAnswerGenerator(
            provider,
            max_attempts=settings.model_max_attempts,
            max_output_tokens=settings.model_max_output_tokens,
        ),
    )
    system_b = ThresholdRagSystem(
        retriever,
        scorer,
        BaselineAnswerGenerator(
            provider,
            max_attempts=settings.model_max_attempts,
            max_output_tokens=settings.model_max_output_tokens,
        ),
        threshold,
    )
    report_a, summary_a = evaluate_baseline_system(examples, system_a)
    report_b, summary_b = evaluate_baseline_system(examples, system_b)
    write_report(report_a, summary_a, *_paths("system_a"))
    write_report(report_b, summary_b, *_paths("system_b"))
    summary_c = system_c_comparable_summary(SYSTEM_C_REPORT, SYSTEM_C_SUMMARY)
    comparison = {
        "evaluation_split": "development",
        "threshold": threshold,
        "systems": {
            "system_a": summary_a,
            "system_b": summary_b,
            "system_c": summary_c,
        },
        "selective_risk": [
            {
                "system": key,
                "coverage": value["gate"]["coverage"],
                "answer_correctness_among_answered": value["answer_quality"]["answer_correctness_among_answered"],
                "abstention_recall": value["gate"]["abstention_recall"],
                "unsupported_answer_rate_on_should_abstain": value["critical_safety"]["unsupported_answer_rate_on_should_abstain"],
            }
            for key, value in (("system_a", summary_a), ("system_b", summary_b))
        ] + [
            {
                "system": "system_c",
                "coverage": summary_c["coverage"],
                "answer_correctness_among_answered": summary_c["answer_correctness_among_answered"],
                "abstention_recall": summary_c["abstention_recall"],
                "unsupported_answer_rate_on_should_abstain": summary_c["unsupported_answer_rate_on_should_abstain"],
            }
        ],
        "scope_note": (
            "Complete-policy comparison: System C also includes deterministic computation and "
            "verified-fact generation. Gate behavior is reported separately."
        ),
    }
    comparison_path = targets[-1]
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(comparison, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
