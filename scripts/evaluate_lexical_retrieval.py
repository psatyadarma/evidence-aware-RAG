"""Build retrieval units and evaluate the deterministic lexical baseline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.retrieval import TfidfRetriever, build_retrieval_units  # noqa: E402
from evaluation.benchmark import load_benchmark, load_evidence_index  # noqa: E402
from evaluation.retrieval_evaluation import (  # noqa: E402
    evaluate_retrieval,
    write_retrieval_report,
)


PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
BENCHMARK_PATH = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
REPORT_PATH = RESULTS_DIR / "lexical_retrieval_report.jsonl"
SUMMARY_PATH = RESULTS_DIR / "lexical_retrieval_summary.json"


def main() -> None:
    units = build_retrieval_units(PROCESSED_DIR)
    retriever = TfidfRetriever(units)
    evidence_index = load_evidence_index(PROCESSED_DIR)
    benchmark = load_benchmark(BENCHMARK_PATH, evidence_index)
    report, summary = evaluate_retrieval(benchmark, retriever)
    summary["corpus_retrieval_units"] = retriever.corpus_size
    write_retrieval_report(report, summary, REPORT_PATH, SUMMARY_PATH)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {SUMMARY_PATH}")


if __name__ == "__main__":
    main()

