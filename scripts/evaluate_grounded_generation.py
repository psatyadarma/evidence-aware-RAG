"""Run one authorized gated generation evaluation on development data only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.answerability import DeterministicAnswerabilityClassifier  # noqa: E402
from app.computation import DeterministicComputationEngine  # noqa: E402
from app.config import Settings  # noqa: E402
from app.generation import GroundedAnswerGenerator  # noqa: E402
from app.pipeline import EvidenceAwarePipeline  # noqa: E402
from app.providers.openai_responses import OpenAIResponsesProvider  # noqa: E402
from app.retrieval import build_retrieval_units  # noqa: E402
from app.structured_retrieval import StructuredRetriever  # noqa: E402
from evaluation.benchmark import load_benchmark, load_evidence_index  # noqa: E402
from evaluation.generation_evaluation import (  # noqa: E402
    evaluate_grounded_generation,
    write_generation_report,
)


PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DEVELOPMENT_BENCHMARK = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
REPORT_PATH = RESULTS_DIR / "development_grounded_generation_v2_report.jsonl"
SUMMARY_PATH = RESULTS_DIR / "development_grounded_generation_v2_summary.json"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the paid grounded-generation evaluation on development data."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Required acknowledgement that this command makes paid network calls.",
    )
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    if not args.live:
        raise SystemExit(
            "Refusing to call a hosted model without explicit --live acknowledgement."
        )
    if REPORT_PATH.exists() or SUMMARY_PATH.exists():
        raise SystemExit(
            "Generation v2 result artifacts already exist; refusing to overwrite the run."
        )
    settings = Settings()
    if settings.model_provider.casefold() != "openai":
        raise SystemExit("Checkpoint 10 supports exactly one live provider: openai.")
    if settings.model_name != "gpt-4.1-mini-2025-04-14":
        raise SystemExit(
            "Checkpoint 10 requires the pinned gpt-4.1-mini-2025-04-14 model."
        )
    if settings.model_api_key is None or not settings.model_api_key.get_secret_value():
        raise SystemExit("RAG_MODEL_API_KEY is required for the live generation run.")

    units = build_retrieval_units(PROCESSED_DIR)
    retriever = StructuredRetriever(units)
    classifier = DeterministicAnswerabilityClassifier(retriever, units)
    provider = OpenAIResponsesProvider(
        api_key=settings.model_api_key.get_secret_value(),
        model=settings.model_name,
        timeout_seconds=settings.model_timeout_seconds,
    )
    generator = GroundedAnswerGenerator(
        provider,
        max_attempts=settings.model_max_attempts,
        max_output_tokens=settings.model_max_output_tokens,
    )
    pipeline = EvidenceAwarePipeline(
        classifier,
        DeterministicComputationEngine(units),
        generator,
        units,
    )
    examples = load_benchmark(
        DEVELOPMENT_BENCHMARK,
        load_evidence_index(PROCESSED_DIR),
    )
    report, summary = evaluate_grounded_generation(examples, pipeline)
    write_generation_report(report, summary, REPORT_PATH, SUMMARY_PATH)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
