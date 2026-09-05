import ast
import json
import re
from pathlib import Path
from typing import Callable, Union

import pytest

from app.answerability import DeterministicAnswerabilityClassifier
from app.computation import DeterministicComputationEngine
from app.generation import (
    OUTPUT_SCHEMA,
    PROMPT_HASH,
    PROMPT_HASH_V1,
    PROMPT_VERSION,
    PROMPT_VERSION_V1,
    GroundedAnswerGenerator,
    build_generation_context,
)
from app.llm_answerability import ProviderCallError, ProviderRequest, ProviderResponse
from app.pipeline import EvidenceAwarePipeline
from app.retrieval import build_retrieval_units
from app.structured_retrieval import StructuredRetriever
from evaluation.benchmark import load_benchmark, load_evidence_index


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
ResponseFactory = Callable[[ProviderRequest], str]
QueuedValue = Union[str, ResponseFactory, Exception]


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
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=25,
            latency_ms=5.0,
        )


@pytest.fixture(scope="module")
def system():
    units = build_retrieval_units(PROCESSED_DIR)
    retriever = StructuredRetriever(units)
    classifier = DeterministicAnswerabilityClassifier(retriever, units)
    engine = DeterministicComputationEngine(units)
    examples = load_benchmark(
        PROJECT_ROOT / "evaluation" / "benchmark.jsonl",
        load_evidence_index(PROCESSED_DIR),
    )
    return units, classifier, engine, {item.id: item for item in examples}


def _valid_answer(request: ProviderRequest) -> str:
    payload = json.loads(request.user_prompt)
    fact = payload["verified_facts"][0]
    qualifications = payload["qualifications"]
    unsupported = payload["unsupported_components"]
    answer = fact["statement"]
    if qualifications:
        answer += " " + " ".join(item["text"] for item in qualifications)
    if unsupported:
        answer += " Limitation: " + " ".join(
            item["description"] + ": " + item["explanation"] for item in unsupported
        )
    return json.dumps(
        {
            "answer": answer,
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
        },
        sort_keys=True,
    )


def _pipeline(system, provider):
    units, classifier, engine, _ = system
    return EvidenceAwarePipeline(
        classifier,
        engine,
        GroundedAnswerGenerator(provider),
        units,
    )


def test_answerable_route_calls_generator_once(system):
    provider = QueueProvider([_valid_answer])
    response = _pipeline(system, provider).answer(system[3]["q001"].question)

    assert response.answer is not None
    assert response.generation_called
    assert not response.abstained
    assert len(provider.requests) == 1


@pytest.mark.parametrize("example_id", ["q014", "q017", "q023", "q025"])
def test_full_abstention_never_calls_generator_or_returns_numeric(system, example_id):
    provider = QueueProvider([])
    response = _pipeline(system, provider).answer(system[3][example_id].question)

    assert response.abstained
    assert not response.generation_called
    assert response.computation is None
    assert response.generated is None
    assert not re.search(r"\b\d[\d,]*\b", response.answer)
    assert provider.requests == []


def test_not_available_is_named_without_becoming_zero(system):
    provider = QueueProvider([])
    response = _pipeline(system, provider).answer(system[3]["q025"].question)

    assert "not available" in response.answer.casefold()
    assert "zero" in response.answer.casefold()


@pytest.mark.parametrize("example_id", ["q011", "q012", "q013"])
def test_partial_route_preserves_unsupported_components(system, example_id):
    provider = QueueProvider([_valid_answer])
    response = _pipeline(system, provider).answer(system[3][example_id].question)

    assert response.generated is not None
    assert response.generation_outcome is not None
    expected = [
        item.component_id
        for item in response.generation_outcome.context.unsupported_components
    ]
    assert response.generated.unsupported_component_ids_addressed == expected
    assert "Limitation:" in response.answer


def test_material_qualification_omission_retries_then_succeeds(system):
    units, classifier, engine, examples = system
    decision = classifier.decide(examples["q013"].question)
    context = build_generation_context(
        examples["q013"].question, decision, engine.compute(decision), units
    )

    def omitted(request):
        output = json.loads(_valid_answer(request))
        output["qualification_ids"] = []
        return json.dumps(output)

    provider = QueueProvider([omitted, _valid_answer])
    outcome = GroundedAnswerGenerator(provider).generate(context)

    assert outcome.answer is not None
    assert len(outcome.attempts) == 2
    assert outcome.attempts[0].error_type == "OutputValidationError"


def test_invented_citation_exhausts_retries(system):
    units, classifier, engine, examples = system
    decision = classifier.decide(examples["q001"].question)
    context = build_generation_context(
        examples["q001"].question, decision, engine.compute(decision), units
    )

    def invented(request):
        output = json.loads(_valid_answer(request))
        output["claims"][0]["evidence_ids"] = ["invented:evidence"]
        return json.dumps(output)

    provider = QueueProvider([invented, invented])
    outcome = GroundedAnswerGenerator(provider).generate(context)

    assert outcome.answer is None
    assert outcome.failure is not None
    assert len(outcome.attempts) == 2


def test_nonretryable_provider_failure_is_explicit_and_has_no_answer(system):
    provider = QueueProvider([ProviderCallError("refused", retryable=False)])
    response = _pipeline(system, provider).answer(system[3]["q001"].question)

    assert response.answer is None
    assert response.failure == "ProviderCallError: refused"
    assert len(provider.requests) == 1


def test_generated_numeric_and_categorical_fields_cannot_change(system):
    units, classifier, engine, examples = system
    decision = classifier.decide(examples["q005"].question)
    context = build_generation_context(
        examples["q005"].question, decision, engine.compute(decision), units
    )

    def changed(request):
        output = json.loads(_valid_answer(request))
        output["claims"][0]["numeric_value"] += 1
        return json.dumps(output)

    outcome = GroundedAnswerGenerator(
        QueueProvider([changed]), max_attempts=1
    ).generate(context)
    assert outcome.failure is not None
    assert "numeric result" in outcome.failure.message


def test_answer_text_cannot_omit_caveat_or_invent_number(system):
    units, classifier, engine, examples = system
    decision = classifier.decide(examples["q013"].question)
    context = build_generation_context(
        examples["q013"].question, decision, engine.compute(decision), units
    )

    def bad_text(request):
        output = json.loads(_valid_answer(request))
        output["answer"] = output["claims"][0]["text"] + " The answer is 999 residents."
        return json.dumps(output)

    outcome = GroundedAnswerGenerator(
        QueueProvider([bad_text]), max_attempts=1
    ).generate(context)

    assert outcome.failure is not None
    assert "qualification" in outcome.failure.message or "numeric value" in outcome.failure.message


def test_prompt_schema_and_runtime_are_frozen_and_benchmark_independent():
    assert PROMPT_VERSION_V1 == "grounded_answer_generation_v1"
    assert PROMPT_HASH_V1 == "f9d44bf1ffa8a8785829d8b7cfa52d37f1a1ed228a5d3af851c8aa2ce05d537b"
    assert PROMPT_VERSION == "grounded_answer_generation_v2"
    assert re.fullmatch(r"[0-9a-f]{64}", PROMPT_HASH)
    assert set(OUTPUT_SCHEMA["required"]) == set(OUTPUT_SCHEMA["properties"])
    source = (PROJECT_ROOT / "app" / "generation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "evaluation" not in imports
    assert re.search(r"\bq\d{3}\b", source, flags=re.IGNORECASE) is None
    assert "expected_answer" not in source


def test_live_generation_result_artifacts_are_frozen():
    import hashlib

    expected = {
        "development_grounded_generation_v1_report.jsonl": "e96adea1c83db7c57593a640ea78d56258c1211544953638dc16845d774cc041",
        "development_grounded_generation_v1_summary.json": "172eea61083df7d496f08e9d4033f1c9ee6d8e49af65fbc841db27481b596200",
        "development_grounded_generation_v2_report.jsonl": "8ef36f2d0d148ae9e3ce2ced98e653a7b74d2e2d3f3ad7ca60759bd96b2d29b2",
        "development_grounded_generation_v2_summary.json": "0ceaa2dac6bc1954f9796bba39549d83f309913c04949c64e7736b63a1050735",
    }
    for name, digest in expected.items():
        content = (PROJECT_ROOT / "evaluation" / "results" / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest
