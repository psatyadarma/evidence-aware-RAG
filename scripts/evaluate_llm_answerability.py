"""Run the explicitly authorized live LLM evaluation on development data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.answerability import DeterministicAnswerabilityClassifier  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm_answerability import LlmEvidenceSufficiencyClassifier  # noqa: E402
from app.providers.openai_responses import OpenAIResponsesProvider  # noqa: E402
from app.retrieval import build_retrieval_units  # noqa: E402
from app.structured_retrieval import StructuredRetriever  # noqa: E402
from evaluation.benchmark import load_benchmark, load_evidence_index  # noqa: E402
from evaluation.llm_answerability_evaluation import (  # noqa: E402
    evaluate_llm_answerability,
    write_llm_answerability_report,
)


PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DEVELOPMENT_BENCHMARK = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
REPORT_PATH = RESULTS_DIR / "development_llm_answerability_report.jsonl"
SUMMARY_PATH = RESULTS_DIR / "development_llm_answerability_summary.json"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run paid hosted-model classification on the development split."
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
    settings = Settings()
    if settings.model_provider.casefold() != "openai":
        raise SystemExit(
            "Checkpoint 9 supports exactly one provider: set RAG_MODEL_PROVIDER=openai."
        )
    if settings.model_api_key is None or not settings.model_api_key.get_secret_value():
        raise SystemExit(
            "RAG_MODEL_API_KEY is required for the live development evaluation."
        )

    units = build_retrieval_units(PROCESSED_DIR)
    retriever = StructuredRetriever(units)
    deterministic = DeterministicAnswerabilityClassifier(retriever, units)
    provider = OpenAIResponsesProvider(
        api_key=settings.model_api_key.get_secret_value(),
        model=settings.model_name,
        timeout_seconds=settings.model_timeout_seconds,
    )
    llm = LlmEvidenceSufficiencyClassifier(
        retriever,
        provider,
        max_attempts=settings.model_max_attempts,
        max_output_tokens=settings.model_max_output_tokens,
    )
    examples = load_benchmark(
        DEVELOPMENT_BENCHMARK,
        load_evidence_index(PROCESSED_DIR),
    )
    report, summary = evaluate_llm_answerability(
        examples, deterministic, llm
    )
    write_llm_answerability_report(report, summary, REPORT_PATH, SUMMARY_PATH)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
