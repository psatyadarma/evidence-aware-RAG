"""Evaluate schema-aware retrieval and write a three-method comparison."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.retrieval import build_retrieval_units  # noqa: E402
from app.structured_retrieval import StructuredRetriever  # noqa: E402
from evaluation.benchmark import load_benchmark, load_evidence_index  # noqa: E402
from evaluation.retrieval_evaluation import write_retrieval_report  # noqa: E402
from evaluation.structured_retrieval_evaluation import (  # noqa: E402
    evaluate_structured_retrieval,
)


PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
BENCHMARK_PATH = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
REPORT_PATH = RESULTS_DIR / "structured_retrieval_report.jsonl"
SUMMARY_PATH = RESULTS_DIR / "structured_retrieval_summary.json"
COMPARISON_PATH = RESULTS_DIR / "three_way_retrieval_comparison.json"
LEXICAL_SUMMARY_PATH = RESULTS_DIR / "lexical_retrieval_summary.json"
SEMANTIC_SUMMARY_PATH = RESULTS_DIR / "semantic_retrieval_summary.json"


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> None:
    units = build_retrieval_units(PROCESSED_DIR)
    retriever = StructuredRetriever(units)
    benchmark = load_benchmark(BENCHMARK_PATH, load_evidence_index(PROCESSED_DIR))
    report, summary = evaluate_structured_retrieval(benchmark, retriever)
    summary["corpus_retrieval_units"] = retriever.corpus_size
    write_retrieval_report(report, summary, REPORT_PATH, SUMMARY_PATH)

    lexical = json.loads(LEXICAL_SUMMARY_PATH.read_text(encoding="utf-8"))
    semantic = json.loads(SEMANTIC_SUMMARY_PATH.read_text(encoding="utf-8"))
    comparison = {
        "ks": summary["ks"],
        "positive_evidence_examples": summary["positive_evidence_examples"],
        "methods": {
            lexical["retrieval_method"]: lexical["overall_mean_recall_at_k"],
            semantic["retrieval_method"]: semantic["overall_mean_recall_at_k"],
            summary["retrieval_method"]: summary["overall_mean_recall_at_k"],
        },
        "structured_full_required_evidence_recovery": summary[
            "overall_full_required_evidence_recovery"
        ],
    }
    _write_json_atomic(COMPARISON_PATH, comparison)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {SUMMARY_PATH}")
    print(f"Wrote {COMPARISON_PATH}")


if __name__ == "__main__":
    main()
