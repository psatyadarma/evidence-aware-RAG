"""Post-retrieval measurement for the deterministic structured method."""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from app.models import BenchmarkExample
from app.structured_retrieval import StructuredRetriever
from evaluation.retrieval_evaluation import (
    POSITIVE_EVIDENCE_CLASSES,
    evaluate_retrieval,
)


def evaluate_structured_retrieval(
    examples: Sequence[BenchmarkExample],
    retriever: StructuredRetriever,
) -> tuple[list[dict], dict]:
    report, summary = evaluate_retrieval(
        examples,
        retriever,
        retrieval_method="structured_schema_v1",
    )
    full_values: list[float] = []
    full_by_category: dict[str, list[float]] = defaultdict(list)

    for example, row in zip(examples, report):
        plan = retriever.plan(example.question)
        all_ids = [result.evidence.evidence_id for result in plan.results]
        eligible = example.answerability in POSITIVE_EVIDENCE_CLASSES
        full_recovery = None
        if eligible:
            required = set(example.required_evidence)
            full_recovery = len(required.intersection(all_ids)) / len(required)
            full_values.append(full_recovery)
            full_by_category[example.category.value].append(full_recovery)
        row.update(
            {
                "structured_query": plan.query.model_dump(mode="json"),
                "structured_diagnostics": plan.diagnostics,
                "structured_result_count": len(all_ids),
                "structured_result_evidence_ids": all_ids,
                "full_required_evidence_recovery": full_recovery,
            }
        )

    summary.update(
        {
            "overall_full_required_evidence_recovery": (
                sum(full_values) / len(full_values) if full_values else 0.0
            ),
            "full_required_evidence_recovery_by_category": {
                name: sum(values) / len(values)
                for name, values in sorted(full_by_category.items())
            },
            "structured_result_definition": (
                "All observations satisfying exact interpreted metadata constraints; "
                "not truncated to a top-k cutoff."
            ),
        }
    )
    return report, summary
