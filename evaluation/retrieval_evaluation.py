"""Benchmark-only evaluation of a retriever; never imported by retrieval code."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence

from app.models import AnswerabilityClassification, BenchmarkExample
from app.retrieval import Retriever
from evaluation.metrics import recall_at_k


POSITIVE_EVIDENCE_CLASSES = frozenset(
    {
        AnswerabilityClassification.ANSWERABLE,
        AnswerabilityClassification.PARTIALLY_ANSWERABLE,
    }
)


def evaluate_retrieval(
    examples: Sequence[BenchmarkExample],
    retriever: Retriever,
    ks: Sequence[int] = (1, 3, 5, 10),
    retrieval_method: str = "tfidf_cosine_v1",
) -> tuple[list[dict], dict]:
    if not ks or any(k <= 0 for k in ks):
        raise ValueError("ks must contain positive integers")
    ordered_ks = tuple(sorted(set(ks)))
    maximum_k = max(ordered_ks)
    report: list[dict] = []
    aggregate_values: dict[int, list[float]] = defaultdict(list)
    category_values: dict[str, dict[int, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    evidence_count_values: dict[int, dict[int, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for example in examples:
        results = retriever.retrieve(example.question, maximum_k)
        retrieved_ids = [result.evidence.evidence_id for result in results]
        eligible = example.answerability in POSITIVE_EVIDENCE_CLASSES
        recalls = {
            str(k): (
                recall_at_k(example.required_evidence, retrieved_ids, k)
                if eligible
                else None
            )
            for k in ordered_ks
        }
        if eligible:
            for k in ordered_ks:
                value = recalls[str(k)]
                if value is None:
                    raise ValueError(
                        f"positive-evidence example {example.id} has no required evidence"
                    )
                aggregate_values[k].append(value)
                category_values[example.category.value][k].append(value)
                evidence_count_values[len(example.required_evidence)][k].append(value)

        ranks = {
            evidence_id: next(
                (
                    result.rank
                    for result in results
                    if result.evidence.evidence_id == evidence_id
                ),
                None,
            )
            for evidence_id in example.required_evidence
        }
        report.append(
            {
                "id": example.id,
                "question": example.question,
                "category": example.category.value,
                "answerability": example.answerability.value,
                "positive_evidence_evaluation": eligible,
                "required_evidence_ids": example.required_evidence,
                "required_evidence_ranks": ranks,
                "retrieved": [
                    {
                        "evidence_id": result.evidence.evidence_id,
                        "rank": result.rank,
                        "score": result.score,
                        "text": result.evidence.text,
                    }
                    for result in results
                ],
                "recall_at_k": recalls,
            }
        )

    def means(values: dict[int, list[float]]) -> dict[str, float]:
        return {
            str(k): sum(values[k]) / len(values[k]) if values[k] else 0.0
            for k in ordered_ks
        }

    summary = {
        "retrieval_method": retrieval_method,
        "benchmark_examples": len(examples),
        "positive_evidence_examples": sum(
            example.answerability in POSITIVE_EVIDENCE_CLASSES for example in examples
        ),
        "excluded_from_positive_metrics": sum(
            example.answerability not in POSITIVE_EVIDENCE_CLASSES for example in examples
        ),
        "ks": list(ordered_ks),
        "overall_mean_recall_at_k": means(aggregate_values),
        "by_category": {
            category: means(values)
            for category, values in sorted(category_values.items())
        },
        "by_required_evidence_count": {
            str(count): means(values)
            for count, values in sorted(evidence_count_values.items())
        },
    }
    return report, summary


def write_retrieval_report(
    report: Iterable[dict], summary: dict, report_path: Path, summary_path: Path
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    report_temp = report_path.with_suffix(report_path.suffix + ".tmp")
    summary_temp = summary_path.with_suffix(summary_path.suffix + ".tmp")
    try:
        with report_temp.open("w", encoding="utf-8", newline="\n") as output:
            for row in report:
                output.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
                output.write("\n")
        with summary_temp.open("w", encoding="utf-8", newline="\n") as output:
            output.write(
                json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
        report_temp.replace(report_path)
        summary_temp.replace(summary_path)
    except Exception:
        report_temp.unlink(missing_ok=True)
        summary_temp.unlink(missing_ok=True)
        raise
