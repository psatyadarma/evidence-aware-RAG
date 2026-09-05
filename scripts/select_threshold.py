"""Select and freeze the one development-only similarity threshold."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sentence_transformers import SentenceTransformer  # noqa: E402

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
from evaluation.threshold_selection import (  # noqa: E402
    ThresholdObservation,
    binary_gate_label,
    select_threshold,
)


PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
EMBEDDING_DIR = PROJECT_ROOT / "data" / "embeddings" / "bge-small-en-v1.5"
DEVELOPMENT_BENCHMARK = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"
OUTPUT_PATH = PROJECT_ROOT / "evaluation" / "results" / "development_threshold_selection.json"


def main() -> None:
    if OUTPUT_PATH.exists():
        raise SystemExit("Threshold selection artifact exists; refusing to overwrite frozen τ.")
    units = build_retrieval_units(PROCESSED_DIR)
    _, embeddings = load_embedding_artifact(
        units,
        EMBEDDING_DIR,
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
    scorer = StructuredEvidenceSimilarityScorer(units, embeddings, encoder)
    retriever = StructuredRetriever(units)
    examples = load_benchmark(
        DEVELOPMENT_BENCHMARK,
        load_evidence_index(PROCESSED_DIR),
    )
    observations = []
    for example in examples:
        plan = retriever.plan(example.question)
        result = scorer.score(example.question, plan.results)
        observations.append(
            ThresholdObservation(
                id=example.id,
                label=binary_gate_label(example.answerability),
                score=result.maximum_similarity,
            )
        )
    selection = select_threshold(observations)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            selection.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(OUTPUT_PATH)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    print(selection.model_dump_json(indent=2))
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
