"""Evaluate deterministic answerability on the development set only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.answerability import DeterministicAnswerabilityClassifier  # noqa: E402
from app.retrieval import build_retrieval_units  # noqa: E402
from app.structured_retrieval import StructuredRetriever  # noqa: E402
from evaluation.answerability_evaluation import (  # noqa: E402
    evaluate_answerability,
    write_answerability_report,
)
from evaluation.benchmark import load_benchmark, load_evidence_index  # noqa: E402


PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DEVELOPMENT_BENCHMARK = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
REPORT_PATH = RESULTS_DIR / "development_answerability_report.jsonl"
SUMMARY_PATH = RESULTS_DIR / "development_answerability_summary.json"


def main() -> None:
    units = build_retrieval_units(PROCESSED_DIR)
    classifier = DeterministicAnswerabilityClassifier(StructuredRetriever(units), units)
    examples = load_benchmark(
        DEVELOPMENT_BENCHMARK,
        load_evidence_index(PROCESSED_DIR),
    )
    report, summary = evaluate_answerability(examples, classifier)
    write_answerability_report(report, summary, REPORT_PATH, SUMMARY_PATH)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
