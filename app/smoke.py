"""Offline runtime smoke check; never invokes the generation provider."""

from __future__ import annotations

import json

from app.models import AnswerabilityClassification
from app.runtime import build_runtime


SMOKE_QUESTION = "What was Singapore's resident population in 2024?"


def run_smoke_check() -> dict:
    runtime = build_runtime()
    decision = runtime.pipeline.classifier.decide(SMOKE_QUESTION)
    if decision.classification != AnswerabilityClassification.ANSWERABLE:
        raise RuntimeError(
            f"smoke question did not resolve as ANSWERABLE: {decision.classification.value}"
        )
    computation = runtime.pipeline.computation_engine.compute(decision)
    fact = computation.facts[0]
    return {
        "status": "ok",
        "provider_called": False,
        "asset_count": runtime.assets.asset_count,
        "retrieval_unit_count": runtime.assets.retrieval_unit_count,
        "classification": decision.classification.value,
        "operation": decision.structured_query.operation.value,
        "retrieved_evidence_count": len(decision.retrieved_evidence_ids),
        "computed_fact_kind": fact.kind.value,
        "computed_evidence_count": len(fact.evidence_ids),
    }


def main() -> None:
    print(json.dumps(run_smoke_check(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
