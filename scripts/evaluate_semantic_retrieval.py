"""Evaluate the pinned BGE retriever against the fixed benchmark."""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402
from sentence_transformers import SentenceTransformer, __version__  # noqa: E402

from app.retrieval import build_retrieval_units  # noqa: E402
from app.semantic_retrieval import (  # noqa: E402
    MODEL_DIMENSION,
    MODEL_ID,
    MODEL_REVISION,
    SEMANTIC_RETRIEVER_NAME,
    SemanticRetriever,
    load_embedding_artifact,
)
from evaluation.benchmark import load_benchmark, load_evidence_index  # noqa: E402
from evaluation.retrieval_evaluation import (  # noqa: E402
    evaluate_retrieval,
    write_retrieval_report,
)


PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
BENCHMARK_PATH = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"
ARTIFACT_DIR = PROJECT_ROOT / "data" / "embeddings" / "bge-small-en-v1.5"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
REPORT_PATH = RESULTS_DIR / "semantic_retrieval_report.jsonl"
SUMMARY_PATH = RESULTS_DIR / "semantic_retrieval_summary.json"
COMPARISON_PATH = RESULTS_DIR / "retrieval_comparison.json"
LEXICAL_SUMMARY_PATH = RESULTS_DIR / "lexical_retrieval_summary.json"


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
            output.write("\n")
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> None:
    units = build_retrieval_units(PROCESSED_DIR)
    metadata, embeddings = load_embedding_artifact(
        units,
        ARTIFACT_DIR,
        expected_model_id=MODEL_ID,
        expected_model_revision=MODEL_REVISION,
        expected_dimension=MODEL_DIMENSION,
    )
    encoder = SentenceTransformer(
        MODEL_ID,
        revision=MODEL_REVISION,
        device="cpu",
        local_files_only=True,
    )
    retriever = SemanticRetriever(units, embeddings, encoder)
    evidence_index = load_evidence_index(PROCESSED_DIR)
    benchmark = load_benchmark(BENCHMARK_PATH, evidence_index)

    report, summary = evaluate_retrieval(
        benchmark,
        retriever,
        retrieval_method=SEMANTIC_RETRIEVER_NAME,
    )
    retriever.retrieve(benchmark[0].question, 10)
    started = perf_counter()
    for example in benchmark:
        retriever.retrieve(example.question, 10)
    mean_latency_ms = (perf_counter() - started) * 1000 / len(benchmark)

    artifact_size = sum(
        path.stat().st_size for path in ARTIFACT_DIR.iterdir() if path.is_file()
    )
    summary.update(
        {
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "corpus_retrieval_units": retriever.corpus_size,
            "embedding_dimension": retriever.embedding_dimension,
            "embedding_matrix_bytes": int(embeddings.nbytes),
            "artifact_size_bytes": artifact_size,
            "embedding_build_duration_seconds": metadata.build_duration_seconds,
            "mean_warm_query_retrieval_latency_ms": mean_latency_ms,
            "measurement_environment": {
                "platform": platform.platform(),
                "processor": (
                    platform.processor()
                    or os.environ.get("PROCESSOR_IDENTIFIER")
                    or "not reported by platform"
                ),
                "python": platform.python_version(),
                "numpy": np.__version__,
                "sentence_transformers": __version__,
                "device": "cpu",
            },
        }
    )
    write_retrieval_report(report, summary, REPORT_PATH, SUMMARY_PATH)

    lexical = json.loads(LEXICAL_SUMMARY_PATH.read_text(encoding="utf-8"))
    comparison = {
        "ks": summary["ks"],
        "positive_evidence_examples": summary["positive_evidence_examples"],
        "methods": {
            "tfidf_cosine_v1": lexical["overall_mean_recall_at_k"],
            SEMANTIC_RETRIEVER_NAME: summary["overall_mean_recall_at_k"],
        },
    }
    _write_json_atomic(COMPARISON_PATH, comparison)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {SUMMARY_PATH}")
    print(f"Wrote {COMPARISON_PATH}")


if __name__ == "__main__":
    main()
