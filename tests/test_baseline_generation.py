import ast
import json
import re
from pathlib import Path

import pytest

from app.baseline_generation import (
    OUTPUT_SCHEMA,
    PROMPT_HASH,
    PROMPT_VERSION,
    BaselineAction,
    BaselineAnswerGenerator,
    build_baseline_context,
)
from app.comparison_systems import ThresholdRagSystem, UngatedRagSystem
from app.llm_answerability import ProviderCallError, ProviderResponse
from app.retrieval import build_retrieval_units
from app.structured_retrieval import StructuredRetriever
from app.threshold_gate import ThresholdScore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


class QueueProvider:
    provider_name = "mock"
    requested_model = "gpt-4.1-mini-2025-04-14"

    def __init__(self, values):
        self.values = list(values)
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        output = value(request) if callable(value) else value
        return ProviderResponse(
            output_text=output,
            model=self.requested_model,
            response_id=f"mock-{len(self.requests)}",
            input_tokens=80,
            cached_input_tokens=0,
            output_tokens=20,
            latency_ms=4.0,
        )


class FixedScorer:
    def __init__(self, score):
        self.fixed = score
        self.calls = 0

    def score(self, question, results):
        self.calls += 1
        return ThresholdScore(
            maximum_similarity=self.fixed if results else None,
            evidence_scores=(
                [{"evidence_id": results[0].evidence.evidence_id, "score": self.fixed}]
                if results
                else []
            ),
            scoring_latency_ms=1.0,
        )


@pytest.fixture(scope="module")
def retriever():
    return StructuredRetriever(build_retrieval_units(PROCESSED_DIR))


def _answer(request):
    payload = json.loads(request.user_prompt)
    evidence_id = payload["evidence"][0]["evidence_id"]
    return json.dumps(
        {
            "action": "ANSWER",
            "answer": "The supplied value answers the question.",
            "claims": [
                {
                    "text": "The supplied value answers the question.",
                    "evidence_ids": [evidence_id],
                    "numeric_value": payload["evidence"][0]["value"],
                    "categorical_value": None,
                }
            ],
            "limitations": [],
        }
    )


def _abstain(request):
    return json.dumps(
        {
            "action": "ABSTAIN",
            "answer": None,
            "claims": [],
            "limitations": ["The evidence is insufficient."],
        }
    )


def test_ungated_always_calls_baseline_even_with_zero_evidence(retriever):
    provider = QueueProvider([_abstain])
    system = UngatedRagSystem(retriever, BaselineAnswerGenerator(provider))

    response = system.answer("What was the household income in Tampines in 2020?")

    assert response.generation_called
    assert response.model_abstained
    assert len(provider.requests) == 1
    assert json.loads(provider.requests[0].user_prompt)["evidence"] == []


def test_threshold_rejection_suppresses_provider_call(retriever):
    provider = QueueProvider([])
    system = ThresholdRagSystem(
        retriever,
        FixedScorer(0.5),
        BaselineAnswerGenerator(provider),
        threshold=0.6,
    )

    response = system.answer("What was Singapore's total population in 2025?")

    assert response.threshold_abstained
    assert not response.generation_called
    assert provider.requests == []


def test_threshold_approval_uses_identical_baseline_context(retriever):
    provider_a = QueueProvider([_answer])
    provider_b = QueueProvider([_answer])
    question = "What was Singapore's total population in 2025?"
    a = UngatedRagSystem(retriever, BaselineAnswerGenerator(provider_a))
    b = ThresholdRagSystem(
        retriever,
        FixedScorer(0.7),
        BaselineAnswerGenerator(provider_b),
        threshold=0.6,
    )

    assert a.answer(question).action == BaselineAction.ANSWER
    assert b.answer(question).action == BaselineAction.ANSWER
    assert provider_a.requests[0].system_prompt == provider_b.requests[0].system_prompt
    assert provider_a.requests[0].user_prompt == provider_b.requests[0].user_prompt
    assert provider_a.requests[0].output_schema == provider_b.requests[0].output_schema


def test_invented_evidence_is_retried_then_fails(retriever):
    context = build_baseline_context(
        "What was Singapore's total population in 2025?",
        retriever.plan("What was Singapore's total population in 2025?").results,
    )

    def invalid(request):
        output = json.loads(_answer(request))
        output["claims"][0]["evidence_ids"] = ["invented:evidence"]
        return json.dumps(output)

    outcome = BaselineAnswerGenerator(QueueProvider([invalid, invalid])).generate(context)

    assert outcome.failure is not None
    assert len(outcome.attempts) == 2
    assert all(item.input_tokens == 80 for item in outcome.attempts)


def test_provider_failure_remains_separate_from_model_abstention(retriever):
    provider = QueueProvider([ProviderCallError("timeout", retryable=False)])
    system = UngatedRagSystem(retriever, BaselineAnswerGenerator(provider))

    response = system.answer("What was Singapore's total population in 2025?")

    assert response.failure == "ProviderCallError: timeout"
    assert not response.model_abstained
    assert response.answer is None


def test_context_excludes_gate_and_benchmark_fields(retriever):
    context = build_baseline_context(
        "What was Singapore's total population in 2025?",
        retriever.plan("What was Singapore's total population in 2025?").results,
    )
    payload = context.model_dump(mode="json")

    assert set(payload) == {"question", "evidence"}
    forbidden = {"classification", "expected_answer", "required_evidence", "diagnostics"}
    assert forbidden.isdisjoint(payload)


def test_prompt_identity_and_schema_are_strict():
    assert PROMPT_VERSION == "baseline_structured_evidence_generation_v1"
    assert re.fullmatch(r"[0-9a-f]{64}", PROMPT_HASH)
    assert set(OUTPUT_SCHEMA["required"]) == set(OUTPUT_SCHEMA["properties"])
    source = (PROJECT_ROOT / "app" / "baseline_generation.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported_roots = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "evaluation" not in imported_roots
    assert "DeterministicAnswerability" not in source
    assert "VerifiedFact" not in source
    assert "BenchmarkExample" not in source
