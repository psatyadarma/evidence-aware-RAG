"""Validate, separate, canonically build, and freeze the held-out benchmark."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.retrieval import build_retrieval_units  # noqa: E402
from evaluation.benchmark import (  # noqa: E402
    BenchmarkValidationError,
    load_benchmark,
    load_evidence_index,
    write_benchmark,
)
from evaluation.benchmark_split import (  # noqa: E402
    BenchmarkSplitError,
    analyse_cross_set_overlap,
    freeze_manifest,
    geography_names,
    require_clean_overlap,
    validate_distinct_ids,
    validate_internal_questions,
    write_json_atomic,
)


PROCESSED_DATA = PROJECT_ROOT / "data" / "processed"
DEVELOPMENT_BENCHMARK = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"
HELDOUT_SEED = PROJECT_ROOT / "evaluation" / "heldout_benchmark_seed.jsonl"
HELDOUT_BENCHMARK = PROJECT_ROOT / "evaluation" / "heldout_benchmark.jsonl"
FREEZE_MANIFEST = PROJECT_ROOT / "evaluation" / "heldout_benchmark.freeze.json"


def main() -> None:
    try:
        evidence_index = load_evidence_index(PROCESSED_DATA)
        development = load_benchmark(DEVELOPMENT_BENCHMARK, evidence_index)
        heldout = load_benchmark(HELDOUT_SEED, evidence_index)
        validate_distinct_ids(development, heldout)
        validate_internal_questions(heldout)
        units = build_retrieval_units(PROCESSED_DATA)
        overlap = analyse_cross_set_overlap(development, heldout, geography_names(units))
        require_clean_overlap(overlap)
        write_benchmark(heldout, HELDOUT_BENCHMARK)
        rebuilt = load_benchmark(HELDOUT_BENCHMARK, evidence_index)
        manifest = freeze_manifest(
            benchmark_path=HELDOUT_BENCHMARK,
            seed_path=HELDOUT_SEED,
            examples=rebuilt,
            overlap_report=overlap,
        )
        write_json_atomic(FREEZE_MANIFEST, manifest)
    except (BenchmarkValidationError, BenchmarkSplitError, OSError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    categories = Counter(example.category.value for example in rebuilt)
    labels = Counter(example.answerability.value for example in rebuilt)
    print(f"Validated and froze {len(rebuilt)} held-out examples")
    print("Categories: " + ", ".join(f"{key}={categories[key]}" for key in sorted(categories)))
    print("Answerability: " + ", ".join(f"{key}={labels[key]}" for key in sorted(labels)))
    print(f"SHA-256: {manifest['benchmark_sha256']}")
    print("Retrieval performance was not evaluated.")


if __name__ == "__main__":
    main()
