import json

import pytest

from app.cli import run as run_cli
from app.controller import ApplicationController
from app.llm_answerability import ProviderCallError, ProviderResponse
from app.runtime import build_runtime
from app.ui import evidence_rows, result_tone, transparency_rows
from app.config import Settings


ANSWERABLE = "What was the resident population of Singapore in 2024?"
PARTIAL = "What was the resident population of Singapore in 2024, and why did it change?"
INSUFFICIENT = "Why did total population change in Singapore in 2024?"
OUT_OF_SCOPE = "What was median household income in Singapore in 2024?"


class GroundedFakeProvider:
    provider_name = "fake"
    requested_model = "gpt-4.1-mini-2025-04-14"

    def __init__(self):
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        context = json.loads(request.user_prompt)
        fact = context["verified_facts"][0]
        required_text = [fact["statement"]]
        required_text.extend(item["text"] for item in context["qualifications"])
        required_text.extend(item["explanation"] for item in context["unsupported_components"])
        output = {
            "answer": " ".join(required_text),
            "claims": [
                {
                    "text": fact["statement"],
                    "fact_id": fact["fact_id"],
                    "evidence_ids": fact["evidence_ids"],
                    "numeric_value": fact["numeric_value"],
                    "categorical_value": fact["categorical_value"],
                }
            ],
            "qualification_ids": [
                item["qualification_id"] for item in context["qualifications"]
            ],
            "unsupported_component_ids_addressed": [
                item["component_id"] for item in context["unsupported_components"]
            ],
        }
        return ProviderResponse(
            output_text=json.dumps(output),
            model=self.requested_model,
            response_id=f"fake-{self.calls}",
            input_tokens=10,
            cached_input_tokens=0,
            output_tokens=10,
            latency_ms=1.0,
        )


class FailingProvider:
    provider_name = "fake"
    requested_model = "gpt-4.1-mini-2025-04-14"

    def generate(self, request):
        del request
        raise ProviderCallError("timeout", retryable=False)


class MalformedProvider:
    provider_name = "fake"
    requested_model = "gpt-4.1-mini-2025-04-14"

    def generate(self, request):
        del request
        return ProviderResponse(
            output_text="not-json",
            model=self.requested_model,
            response_id="malformed-1",
            input_tokens=1,
            cached_input_tokens=0,
            output_tokens=1,
            latency_ms=1.0,
        )


def _settings():
    return Settings(_env_file=None, model_api_key=None)


@pytest.fixture(scope="module")
def runtime():
    return build_runtime(settings=_settings(), provider=GroundedFakeProvider())


@pytest.fixture(scope="module")
def controller(runtime):
    return ApplicationController(runtime)


def test_empty_question_does_not_call_pipeline(controller, runtime):
    before = runtime.pipeline.generator.provider.calls
    result = controller.answer("   ")

    assert result.status == "INPUT_ERROR"
    assert result.error_code == "EMPTY_QUESTION"
    assert runtime.pipeline.generator.provider.calls == before


def test_answerable_controller_matches_direct_pipeline(runtime, controller):
    direct = runtime.pipeline.answer(ANSWERABLE)
    presented = controller.answer(ANSWERABLE)

    assert presented.status == direct.classification.value
    assert presented.answer == direct.answer
    assert presented.generation_invoked == direct.generation_called
    assert result_tone(presented) == "success"
    assert presented.evidence


def test_partial_rendering_includes_limitation_and_evidence(controller):
    result = controller.answer(PARTIAL)

    assert result.status == "PARTIALLY_ANSWERABLE"
    assert result.answer
    assert result.limitations == ["The descriptive observations do not establish a cause."]
    assert result.evidence
    assert result_tone(result) == "warning"


def test_insufficient_rendering_explains_refusal_without_generation(controller, runtime):
    before = runtime.pipeline.generator.provider.calls
    result = controller.answer(INSUFFICIENT)

    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.answer is None
    assert "descriptive observations" in result.message
    assert result.refusal_reasons == ["Causal Evidence Missing"]
    assert not result.generation_invoked
    assert runtime.pipeline.generator.provider.calls == before


def test_out_of_scope_rendering_describes_boundary(controller, runtime):
    before = runtime.pipeline.generator.provider.calls
    result = controller.answer(OUT_OF_SCOPE)

    assert result.status == "OUT_OF_SCOPE"
    assert "outside this population corpus" in result.message
    assert not result.generation_invoked
    assert runtime.pipeline.generator.provider.calls == before


def test_evidence_and_transparency_rendering_surface_provenance(controller):
    result = controller.answer(ANSWERABLE)
    rows = evidence_rows(result.evidence)
    transparency = dict(transparency_rows(result))

    assert rows[0]["Dataset"] == "Indicators On Population, Annual"
    assert rows[0]["Publisher"] == "Singapore Department of Statistics (SingStat)"
    assert rows[0]["Source URL"].startswith("https://data.gov.sg/")
    assert rows[0]["Evidence ID"].startswith("population_indicators_annual:")
    assert transparency["Answerability decision"] == "ANSWERABLE"
    assert transparency["LLM generation invoked"] == "True"


def test_qualification_rendering_for_planning_region(runtime):
    result = ApplicationController(runtime).answer(
        "What was the resident population of East Region in 2024?"
    )

    assert result.status == "ANSWERABLE"
    assert any("rounded to the nearest 10" in item for item in result.qualifications)


def test_missing_api_key_is_deferred_until_supported_generation():
    controller = ApplicationController(build_runtime(settings=_settings()))

    refusal = controller.answer(OUT_OF_SCOPE)
    supported = controller.answer(ANSWERABLE)

    assert refusal.status == "OUT_OF_SCOPE"
    assert refusal.error_code is None
    assert supported.error_code == "MISSING_API_KEY"
    assert "RAG_MODEL_API_KEY" in supported.message


@pytest.mark.parametrize(
    ("provider", "expected_code"),
    [(FailingProvider(), "PROVIDER_ERROR"), (MalformedProvider(), "MALFORMED_MODEL_RESPONSE")],
)
def test_provider_and_malformed_response_fail_closed(provider, expected_code):
    runtime = build_runtime(settings=_settings(), provider=provider)
    result = ApplicationController(runtime).answer(ANSWERABLE)

    assert result.status == "ERROR"
    assert result.answer is None
    assert result.error_code == expected_code


def test_cli_calls_same_controller_pipeline(monkeypatch, runtime):
    monkeypatch.setattr("app.cli.build_runtime", lambda: runtime)

    exit_code, payload = run_cli(INSUFFICIENT)

    assert exit_code == 0
    assert payload["status"] == "INSUFFICIENT_EVIDENCE"
    assert payload["generation_invoked"] is False


def test_info_logs_do_not_contain_full_question(controller, caplog):
    private_question = "Why did total population change in Singapore in 2024?"

    with caplog.at_level("INFO"):
        controller.answer(private_question)

    assert private_question not in caplog.text
