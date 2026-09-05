import json
from pathlib import Path

from app.baseline_generation import BaselineAnswerGenerator
from app.comparison_systems import UngatedRagSystem
from app.llm_answerability import ProviderCallError, ProviderResponse
from app.retrieval import build_retrieval_units
from app.structured_retrieval import StructuredRetriever
from evaluation.benchmark import load_benchmark, load_evidence_index
from evaluation.system_comparison import evaluate_baseline_system


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


class QueueProvider:
    provider_name = "mock"
    requested_model = "gpt-4.1-mini-2025-04-14"

    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        output = value(request)
        return ProviderResponse(
            output_text=output,
            model=self.requested_model,
            response_id=f"mock-{self.calls}",
            input_tokens=80,
            cached_input_tokens=0,
            output_tokens=20,
            latency_ms=4.0,
        )


def _numeric_answer(request):
    payload = json.loads(request.user_prompt)
    item = payload["evidence"][0]
    return json.dumps(
        {
            "action": "ANSWER",
            "answer": f"The value was {item['value']}.",
            "claims": [
                {
                    "text": "population value",
                    "evidence_ids": [item["evidence_id"]],
                    "numeric_value": item["value"],
                    "categorical_value": None,
                }
            ],
            "limitations": [],
        }
    )


def _unsupported_answer(request):
    payload = json.loads(request.user_prompt)
    item = payload["evidence"][0]
    return json.dumps(
        {
            "action": "ANSWER",
            "answer": "The evidence explains the requested cause.",
            "claims": [
                {
                    "text": "causal answer",
                    "evidence_ids": [item["evidence_id"]],
                    "numeric_value": None,
                    "categorical_value": "cause",
                }
            ],
            "limitations": [],
        }
    )


def test_metrics_separate_unsupported_answers_and_provider_failures():
    units = build_retrieval_units(PROCESSED_DIR)
    retriever = StructuredRetriever(units)
    all_examples = load_benchmark(
        PROJECT_ROOT / "evaluation" / "benchmark.jsonl",
        load_evidence_index(PROCESSED_DIR),
    )
    examples = [all_examples[0], all_examples[13], all_examples[22]]
    provider = QueueProvider(
        [_numeric_answer, _unsupported_answer, ProviderCallError("timeout", retryable=False)]
    )
    system = UngatedRagSystem(
        retriever, BaselineAnswerGenerator(provider, max_attempts=1)
    )

    report, summary = evaluate_baseline_system(examples, system)

    assert summary["gate"]["coverage"] == 2 / 3
    assert summary["gate"]["provider_failures"] == 1
    assert summary["critical_safety"]["should_abstain_count"] == 2
    assert summary["critical_safety"]["unsupported_answer_count"] == 1
    assert summary["critical_safety"]["unsupported_answer_rate_on_should_abstain"] == 0.5
    assert summary["critical_safety"]["provider_failure_count"] == 1
    assert summary["answer_quality"]["answer_correctness_among_answered"] == 0.5
    assert summary["cost_and_latency"]["llm_calls_including_retries"] == 3
    assert summary["cost_and_latency"]["estimated_cost_usd"] == 0.000128
    assert report[1]["error_category"] == "answered_unsupported_causal_request"
    assert report[2]["error_category"] == "model_failure"


def test_development_runner_does_not_reference_reserved_benchmark():
    source = (PROJECT_ROOT / "scripts" / "evaluate_development_systems.py").read_text(
        encoding="utf-8"
    )
    assert "DEVELOPMENT_BENCHMARK" in source
    assert "held" + "out" not in source.casefold()
