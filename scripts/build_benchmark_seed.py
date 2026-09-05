"""Validate curated benchmark wording and rebuild canonical benchmark JSONL."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import (  # noqa: E402
    BenchmarkValidationError,
    load_benchmark,
    load_evidence_index,
    write_benchmark,
)


CURATED_SEED = PROJECT_ROOT / "evaluation" / "benchmark_seed.jsonl"
BENCHMARK = PROJECT_ROOT / "evaluation" / "benchmark.jsonl"
PROCESSED_DATA = PROJECT_ROOT / "data" / "processed"


def main() -> None:
    try:
        evidence_index = load_evidence_index(PROCESSED_DATA)
        examples = load_benchmark(CURATED_SEED, evidence_index)
        write_benchmark(examples, BENCHMARK)
        rebuilt = load_benchmark(BENCHMARK, evidence_index)
    except (BenchmarkValidationError, OSError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    categories = Counter(example.category.value for example in rebuilt)
    labels = Counter(example.answerability.value for example in rebuilt)
    print(f"Validated {len(rebuilt)} curated benchmark examples")
    print("Categories: " + ", ".join(f"{key}={categories[key]}" for key in sorted(categories)))
    print("Answerability: " + ", ".join(f"{key}={labels[key]}" for key in sorted(labels)))
    print(f"Wrote {BENCHMARK}")


if __name__ == "__main__":
    main()

