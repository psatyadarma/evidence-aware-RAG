import json
from pathlib import Path

from app.answerability import DeterministicAnswerabilityClassifier
from app.computation import DeterministicComputationEngine
from app.generation import GroundedAnswerGenerator
from app.llm_answerability import ProviderResponse
from app.pipeline import EvidenceAwarePipeline
from app.retrieval import build_retrieval_units
from app.structured_retrieval import StructuredRetriever
from evaluation.benchmark import load_benchmark, load_evidence_index
from evaluation.generation_evaluation import evaluate_grounded_generation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


class ValidProvider:
    provider_name = "mock"
    requested_model = "gpt-4.1-mini-2025-04-14"

    def __init__(self):
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        payload = json.loads(request.user_prompt)
        fact = payload["verified_facts"][0]
        qualifications = payload["qualifications"]
        unsupported = payload["unsupported_components"]
        output = {
            "answer": " ".join(
                [fact["statement"]]
                + [item["text"] for item in qualifications]
                + [item["explanation"] for item in unsupported]
            ),
            "claims": [
                {
                    "text": fact["statement"],
                    "fact_id": fact["fact_id"],
                    "evidence_ids": fact["evidence_ids"],
                    "numeric_value": fact["numeric_value"],
                    "categorical_value": fact["categorical_value"],
                }
            ],
            "qualification_ids": [item["qualification_id"] for item in qualifications],
            "unsupported_component_ids_addressed": [
                item["component_id"] for item in unsupported
            ],
        }
        return ProviderResponse(
            output_text=json.dumps(output),
            model=self.requested_model,
            response_id=f"mock-{self.calls}",
            input_tokens=100,
            cached_input_tokens=10,
            output_tokens=25,
            latency_ms=5.0,
        )


def test_development_evaluation_tracks_gate_grounding_and_cost():
    units = build_retrieval_units(PROCESSED_DIR)
    retriever = StructuredRetriever(units)
    classifier = DeterministicAnswerabilityClassifier(retriever, units)
    provider = ValidProvider()
    pipeline = EvidenceAwarePipeline(
        classifier,
        DeterministicComputationEngine(units),
        GroundedAnswerGenerator(provider),
        units,
    )
    all_examples = load_benchmark(
        PROJECT_ROOT / "evaluation" / "benchmark.jsonl",
        load_evidence_index(PROCESSED_DIR),
    )
    examples = [all_examples[0], all_examples[10], all_examples[22]]

    report, summary = evaluate_grounded_generation(examples, pipeline)

    assert len(report) == 3
    assert provider.calls == 2
    assert summary["routing"]["calls_avoided_by_gate"] == 1
    assert summary["routing"]["full_abstentions_with_provider_call"] == 0
    assert summary["routing"]["full_abstentions_with_numeric_answer"] == 0
    assert summary["computation"]["accuracy"] == 1.0
    assert summary["grounding"]["citation_valid_rate"] == 1.0
    assert summary["grounding"]["required_evidence_coverage_rate"] == 1.0
    assert summary["grounding"]["structurally_unsupported_claim_rate"] == 0.0
    assert summary["future_ungated_comparison"]["status"] == "designed_not_run"
    assert summary["cost_and_latency"]["input_tokens"] == 200


def test_live_script_is_development_only_and_refuses_implicit_calls():
    source = (
        PROJECT_ROOT / "scripts" / "evaluate_grounded_generation.py"
    ).read_text(encoding="utf-8")

    assert "DEVELOPMENT_BENCHMARK" in source
    assert "--live" in source
    assert "held" + "out" not in source.casefold()
