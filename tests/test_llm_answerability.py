import ast
import json
import re
import urllib.error
from pathlib import Path
from typing import Callable, Union

import pytest
from pydantic import ValidationError

from app.llm_answerability import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    PROMPT_HASH,
    PROMPT_VERSION,
    LlmClassificationOutcome,
    LlmEvidenceSufficiencyClassifier,
    LlmEvidenceSufficiencyDecision,
    ProviderCallError,
    ProviderRequest,
    ProviderResponse,
    serialize_context,
)
from app.providers.openai_responses import OpenAIResponsesProvider
from app.answerability import DeterministicAnswerabilityClassifier
from app.retrieval import build_retrieval_units
from app.structured_retrieval import StructuredRetriever
from evaluation.benchmark import load_benchmark, load_evidence_index
from evaluation.llm_answerability_evaluation import evaluate_llm_answerability


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
ResponseFactory = Callable[[ProviderRequest], str]
QueuedValue = Union[str, ResponseFactory, Exception]


class QueueProvider:
    provider_name = "mock"
    requested_model = "mock-model-v1"

    def __init__(self, values: list[QueuedValue]) -> None:
        self.values = list(values)
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
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
            output_tokens=20,
            latency_ms=12.5,
        )


@pytest.fixture(scope="module")
def retriever() -> StructuredRetriever:
    return StructuredRetriever(build_retrieval_units(PROCESSED_DIR))


def _supported_output(request: ProviderRequest) -> str:
    payload = json.loads(request.user_prompt)
    evidence_id = payload["evidence"][0]["evidence_id"]
    return json.dumps(
        {
            "classification": "ANSWERABLE",
            "claims": [
                {
                    "claim": "requested population observation",
                    "support": "SUPPORTED",
                    "evidence_ids": [evidence_id],
                    "reason": "The exact requested observation is present.",
                }
            ],
            "decision_evidence_ids": [evidence_id],
            "reason_codes": ["EVIDENCE_COMPLETE"],
            "reason": "All requested claims have complete evidence.",
        },
        sort_keys=True,
    )


def _partial_output(request: ProviderRequest) -> str:
    payload = json.loads(request.user_prompt)
    evidence_id = payload["evidence"][0]["evidence_id"]
    return json.dumps(
        {
            "classification": "PARTIALLY_ANSWERABLE",
            "claims": [
                {
                    "claim": "historical population observation",
                    "support": "SUPPORTED",
                    "evidence_ids": [evidence_id],
                    "reason": "The historical value is supplied.",
                },
                {
                    "claim": "future population forecast",
                    "support": "UNSUPPORTED",
                    "evidence_ids": [],
                    "reason": "Historical values do not establish a forecast.",
                },
            ],
            "decision_evidence_ids": [evidence_id],
            "reason_codes": ["PARTIAL_SUPPORT", "FORECAST_UNSUPPORTED"],
            "reason": "The historical subrequest is supported; the forecast is not.",
        },
        sort_keys=True,
    )


def test_prompt_and_evidence_are_compact_typed_and_deterministic(
    retriever: StructuredRetriever,
) -> None:
    provider = QueueProvider([_supported_output, _supported_output])
    classifier = LlmEvidenceSufficiencyClassifier(retriever, provider)
    first = classifier.classify(
        "Please report Singapore's resident population for 2022."
    )
    second = classifier.classify(
        "Please report Singapore's resident population for 2022."
    )

    assert first.decision is not None
    assert first.decision == second.decision
    assert first.context == second.context
    assert first.prompt_hash == second.prompt_hash
    assert provider.requests[0].user_prompt == provider.requests[1].user_prompt
    assert provider.requests[0].temperature == 0.0
    assert provider.requests[0].max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS
    assert provider.requests[0].output_schema == LlmEvidenceSufficiencyDecision.model_json_schema()
    payload = json.loads(provider.requests[0].user_prompt)
    assert len(payload["evidence"]) == 1
    assert payload["evidence"][0]["status"] == "OBSERVED"
    assert payload["evidence"][0]["dataset_name"]
    assert payload["evidence"][0]["qualifications"]
    assert "ground_truth" not in provider.requests[0].user_prompt
    assert PROMPT_VERSION == first.prompt_version
    assert re.fullmatch(r"[0-9a-f]{64}", PROMPT_HASH)


def test_canonical_serializer_is_order_stable(retriever: StructuredRetriever) -> None:
    provider = QueueProvider([_supported_output])
    outcome = LlmEvidenceSufficiencyClassifier(retriever, provider).classify(
        "Please report Singapore's resident population for 2022."
    )
    assert serialize_context(outcome.context) == provider.requests[0].user_prompt
    assert serialize_context(outcome.context) == serialize_context(outcome.context)


def test_partial_answer_schema(retriever: StructuredRetriever) -> None:
    provider = QueueProvider([_partial_output])
    outcome = LlmEvidenceSufficiencyClassifier(retriever, provider).classify(
        "Report Singapore's total population in 2025, and forecast its value for 2030."
    )

    assert outcome.decision is not None
    assert outcome.decision.classification.value == "PARTIALLY_ANSWERABLE"
    assert [claim.support.value for claim in outcome.decision.claims] == [
        "SUPPORTED",
        "UNSUPPORTED",
    ]


def test_malformed_json_is_retried_then_accepted(
    retriever: StructuredRetriever,
) -> None:
    provider = QueueProvider(["not-json", _supported_output])
    outcome = LlmEvidenceSufficiencyClassifier(retriever, provider).classify(
        "Please report Singapore's resident population for 2022."
    )

    assert outcome.decision is not None
    assert len(outcome.attempts) == 2
    assert outcome.attempts[0].error_type == "OutputValidationError"
    assert outcome.attempts[1].valid is True


def test_invalid_label_is_not_coerced(retriever: StructuredRetriever) -> None:
    invalid = json.dumps(
        {
            "classification": "MAYBE",
            "claims": [],
            "decision_evidence_ids": [],
            "reason_codes": ["EVIDENCE_COMPLETE"],
            "reason": "invalid",
        }
    )
    provider = QueueProvider([invalid, invalid])
    outcome = LlmEvidenceSufficiencyClassifier(retriever, provider).classify(
        "Please report Singapore's resident population for 2022."
    )

    assert outcome.decision is None
    assert outcome.failure is not None
    assert outcome.failure.error_type == "OutputValidationError"
    assert len(outcome.attempts) == 2


def test_missing_required_output_fields_are_not_coerced(
    retriever: StructuredRetriever,
) -> None:
    incomplete = json.dumps({"classification": "ANSWERABLE"})
    provider = QueueProvider([incomplete, incomplete])
    outcome = LlmEvidenceSufficiencyClassifier(retriever, provider).classify(
        "Please report Singapore's resident population for 2022."
    )

    assert outcome.decision is None
    assert outcome.failure is not None
    assert "schema-invalid" in outcome.failure.message


def test_not_available_is_serialized_without_zero_substitution(
    retriever: StructuredRetriever,
) -> None:
    insufficient = json.dumps(
        {
            "classification": "INSUFFICIENT_EVIDENCE",
            "claims": [
                {
                    "claim": "Simpang resident count",
                    "support": "UNAVAILABLE",
                    "evidence_ids": [],
                    "reason": "The matching published cell is unavailable.",
                }
            ],
            "decision_evidence_ids": [],
            "reason_codes": ["VALUE_NOT_AVAILABLE"],
            "reason": "No usable numeric value is supplied.",
        }
    )
    provider = QueueProvider([insufficient])
    outcome = LlmEvidenceSufficiencyClassifier(retriever, provider).classify(
        "Return the all-sex, all-age Census 2020 resident count for Simpang planning area."
    )

    payload = json.loads(provider.requests[0].user_prompt)
    unavailable = [
        item for item in payload["evidence"] if item["status"] == "NOT_AVAILABLE"
    ]
    assert unavailable
    assert all(item["value"] is None for item in unavailable)
    assert any("not zero" in note.casefold() for item in unavailable for note in item["qualifications"])


def test_nonexistent_evidence_id_is_rejected(retriever: StructuredRetriever) -> None:
    def invalid_citation(_: ProviderRequest) -> str:
        return json.dumps(
            {
                "classification": "ANSWERABLE",
                "claims": [
                    {
                        "claim": "population value",
                        "support": "SUPPORTED",
                        "evidence_ids": ["invented:evidence:id"],
                        "reason": "purported support",
                    }
                ],
                "decision_evidence_ids": ["invented:evidence:id"],
                "reason_codes": ["EVIDENCE_COMPLETE"],
                "reason": "purported evidence",
            }
        )

    provider = QueueProvider([invalid_citation, invalid_citation])
    outcome = LlmEvidenceSufficiencyClassifier(retriever, provider).classify(
        "Please report Singapore's resident population for 2022."
    )

    assert outcome.decision is None
    assert outcome.failure is not None
    assert "not supplied" in outcome.failure.message


def test_provider_timeout_retries_without_prediction(
    retriever: StructuredRetriever,
) -> None:
    timeout = ProviderCallError("provider timeout", retryable=True)
    provider = QueueProvider([timeout, timeout])
    outcome = LlmEvidenceSufficiencyClassifier(retriever, provider).classify(
        "Please report Singapore's resident population for 2022."
    )

    assert outcome.decision is None
    assert outcome.failure is not None
    assert len(outcome.attempts) == 2


def test_nonretryable_provider_error_stops_immediately(
    retriever: StructuredRetriever,
) -> None:
    provider = QueueProvider(
        [ProviderCallError("bad credentials", retryable=False), _supported_output]
    )
    outcome = LlmEvidenceSufficiencyClassifier(retriever, provider).classify(
        "Please report Singapore's resident population for 2022."
    )

    assert outcome.decision is None
    assert len(outcome.attempts) == 1


def test_output_schema_rejects_claim_class_mismatch() -> None:
    with pytest.raises(ValidationError, match="PARTIALLY_ANSWERABLE"):
        LlmEvidenceSufficiencyDecision.model_validate(
            {
                "classification": "PARTIALLY_ANSWERABLE",
                "claims": [
                    {
                        "claim": "only claim",
                        "support": "SUPPORTED",
                        "evidence_ids": ["e1"],
                        "reason": "present",
                    }
                ],
                "decision_evidence_ids": ["e1"],
                "reason_codes": ["PARTIAL_SUPPORT"],
                "reason": "inconsistent",
            }
        )


def test_outcome_requires_decision_xor_failure(
    retriever: StructuredRetriever,
) -> None:
    provider = QueueProvider([_supported_output])
    valid = LlmEvidenceSufficiencyClassifier(retriever, provider).classify(
        "Please report Singapore's resident population for 2022."
    )
    payload = valid.model_dump()
    payload["failure"] = {"error_type": "x", "message": "x"}
    with pytest.raises(ValidationError, match="exactly one"):
        LlmClassificationOutcome.model_validate(payload)


class _HttpResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_HttpResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_openai_transport_sets_structured_output_and_no_storage() -> None:
    captured = {}

    def opener(request: object, timeout: float) -> _HttpResponse:
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _HttpResponse(
            {
                "id": "resp_1",
                "model": "gpt-4.1-mini-2025-04-14",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "{\"ok\":true}"}
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 120,
                    "input_tokens_details": {"cached_tokens": 20},
                    "output_tokens": 15,
                },
            }
        )

    provider = OpenAIResponsesProvider(
        api_key="test-only",
        model="gpt-4.1-mini-2025-04-14",
        opener=opener,
    )
    response = provider.generate(
        ProviderRequest(
            system_prompt="instructions",
            user_prompt="{}",
            output_schema={"type": "object"},
            temperature=0.0,
            max_output_tokens=100,
        )
    )

    assert captured["body"]["store"] is False
    assert captured["body"]["temperature"] == 0.0
    assert captured["body"]["text"]["format"]["type"] == "json_schema"
    assert captured["body"]["text"]["format"]["strict"] is True
    assert response.cached_input_tokens == 20


def test_openai_transport_marks_auth_error_nonretryable() -> None:
    def opener(*_: object, **__: object) -> object:
        raise urllib.error.HTTPError("url", 401, "unauthorized", {}, None)

    provider = OpenAIResponsesProvider(
        api_key="test-only", model="test", opener=opener
    )
    with pytest.raises(ProviderCallError) as exc_info:
        provider.generate(
            ProviderRequest(
                system_prompt="x",
                user_prompt="{}",
                output_schema={"type": "object"},
                temperature=0.0,
                max_output_tokens=100,
            )
        )
    assert exc_info.value.retryable is False


def test_development_evaluator_records_direct_comparison_and_cost(
    retriever: StructuredRetriever,
) -> None:
    all_examples = load_benchmark(
        PROJECT_ROOT / "evaluation" / "benchmark.jsonl",
        load_evidence_index(PROCESSED_DIR),
    )
    examples = [all_examples[0], all_examples[11]]
    provider = QueueProvider([_supported_output, _partial_output])
    llm = LlmEvidenceSufficiencyClassifier(retriever, provider)
    deterministic = DeterministicAnswerabilityClassifier(
        retriever, build_retrieval_units(PROCESSED_DIR)
    )

    report, summary = evaluate_llm_answerability(
        examples, deterministic, llm
    )

    assert len(report) == 2
    assert all(row["comparison_outcome"] == "both_correct" for row in report)
    assert summary["evaluation_split"] == "development"
    assert summary["llm"]["accuracy"] == 1.0
    assert summary["cost_and_latency"]["total_provider_calls"] == 2
    assert summary["cost_and_latency"]["input_tokens"] == 200
    assert summary["cost_and_latency"]["output_tokens"] == 40


def test_evaluator_keeps_model_failure_out_of_answerability_labels(
    retriever: StructuredRetriever,
) -> None:
    example = load_benchmark(
        PROJECT_ROOT / "evaluation" / "benchmark.jsonl",
        load_evidence_index(PROCESSED_DIR),
    )[0]
    provider = QueueProvider(
        [
            ProviderCallError("timeout", retryable=True),
            ProviderCallError("timeout", retryable=True),
        ]
    )
    llm = LlmEvidenceSufficiencyClassifier(retriever, provider)
    deterministic = DeterministicAnswerabilityClassifier(
        retriever, build_retrieval_units(PROCESSED_DIR)
    )

    report, summary = evaluate_llm_answerability([example], deterministic, llm)

    assert report[0]["llm_prediction"] is None
    assert summary["llm"]["failure_count"] == 1
    assert summary["llm"]["confusion_matrix"]["ANSWERABLE"]["MODEL_FAILURE"] == 1
    assert summary["llm"]["abstention"]["predicted_abstentions"] == 0


def test_app_classifier_has_no_evaluation_or_split_dependency() -> None:
    path = PROJECT_ROOT / "app" / "llm_answerability.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert "evaluation" not in imported_roots
    assert "BenchmarkExample" not in source
    assert re.search(r"\b[qt]\d{3}\b", source, flags=re.IGNORECASE) is None


def test_live_runner_names_development_input_only() -> None:
    source = (
        PROJECT_ROOT / "scripts" / "evaluate_llm_answerability.py"
    ).read_text(encoding="utf-8")

    assert '"evaluation" / "benchmark.jsonl"' in source
    forbidden_split_name = "held" + "out"
    assert forbidden_split_name not in source.casefold()
    assert "--live" in source
