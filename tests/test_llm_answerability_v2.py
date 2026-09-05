import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Union

import pytest
from pydantic import ValidationError

from app.answerability import DeterministicAnswerabilityClassifier
from app.llm_answerability import ProviderCallError, ProviderRequest, ProviderResponse
from app.llm_answerability_v2 import (
    OUTPUT_SCHEMA_V2,
    PROMPT_HASH_V2,
    PROMPT_VERSION_V2,
    DerivedEvidenceSufficiencyDecisionV2,
    LlmEvidenceAssessmentV2,
    LlmEvidenceSufficiencyClassifierV2,
    PremiseAssessmentV2,
    RequestedClaimAssessmentV2,
    derive_answerability,
)
from app.models import AnswerabilityClassification
from app.retrieval import build_retrieval_units
from app.structured_retrieval import StructuredRetriever
from evaluation.benchmark import load_benchmark, load_evidence_index
from evaluation.llm_answerability_v2_evaluation import (
    evaluate_llm_answerability_v2,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
ResponseFactory = Callable[[ProviderRequest], str]
QueuedValue = Union[str, ResponseFactory, Exception]


class QueueProvider:
    provider_name = "mock"
    requested_model = "gpt-4.1-mini-2025-04-14"

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
            response_id=f"mock-v2-{len(self.requests)}",
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=20,
            latency_ms=10.0,
        )


@pytest.fixture(scope="module")
def retriever() -> StructuredRetriever:
    return StructuredRetriever(build_retrieval_units(PROCESSED_DIR))


def _evidence_ids(request: ProviderRequest) -> list[str]:
    return [
        item["evidence_id"] for item in json.loads(request.user_prompt)["evidence"]
    ]


def _assessment(
    requested_claims: list[dict],
    *,
    premises: list[dict] = None,
    reason_codes: list[str] = None,
    summary: str = "Concise evidence assessment.",
) -> str:
    return json.dumps(
        {
            "requested_claims": requested_claims,
            "premises": premises or [],
            "reason_codes": reason_codes or ["EVIDENCE_COMPLETE"],
            "summary": summary,
        },
        sort_keys=True,
    )


def _supported(request: ProviderRequest) -> str:
    evidence_id = _evidence_ids(request)[0]
    return _assessment(
        [
            {
                "claim": "requested population observation",
                "support": "SUPPORTED",
                "evidence": [
                    {"evidence_id": evidence_id, "relation": "SUPPORTS"}
                ],
                "reason": "The exact observation is supplied.",
            }
        ]
    )


def _compound_partial(request: ProviderRequest) -> str:
    evidence_id = _evidence_ids(request)[0]
    return _assessment(
        [
            {
                "claim": "historical population value",
                "support": "SUPPORTED",
                "evidence": [
                    {"evidence_id": evidence_id, "relation": "SUPPORTS"}
                ],
                "reason": "The historical observation is supplied.",
            },
            {
                "claim": "future population forecast",
                "support": "UNSUPPORTED",
                "evidence": [
                    {"evidence_id": evidence_id, "relation": "CONTEXT"}
                ],
                "reason": "Historical context does not establish a forecast.",
            },
        ],
        reason_codes=["PARTIAL_SUPPORT", "FORECAST_UNSUPPORTED"],
    )


def _pure_causal_with_supported_premise(request: ProviderRequest) -> str:
    ids = _evidence_ids(request)
    related = [{"evidence_id": item, "relation": "CONTEXT"} for item in ids]
    premise_evidence = [
        {"evidence_id": item, "relation": "SUPPORTS"} for item in ids
    ]
    return _assessment(
        [
            {
                "claim": "cause of the population change",
                "support": "UNSUPPORTED",
                "evidence": related,
                "reason": "Descriptive observations do not establish a cause.",
            }
        ],
        premises=[
            {
                "premise": "the population changed in the asserted direction",
                "status": "SUPPORTED",
                "evidence": premise_evidence,
                "reason": "The supplied values establish the direction only.",
            }
        ],
        reason_codes=["CAUSAL_EVIDENCE_MISSING"],
    )


def _contradicted_premise(request: ProviderRequest) -> str:
    ids = _evidence_ids(request)
    return _assessment(
        [
            {
                "claim": "cause of the asserted decline",
                "support": "UNSUPPORTED",
                "evidence": [
                    {"evidence_id": item, "relation": "CONTEXT"} for item in ids
                ],
                "reason": "No causal evidence is supplied.",
            }
        ],
        premises=[
            {
                "premise": "the population declined",
                "status": "CONTRADICTED",
                "evidence": [
                    {"evidence_id": item, "relation": "CONTRADICTS"}
                    for item in ids
                ],
                "reason": "The supplied observations contradict the decline.",
            }
        ],
        reason_codes=["FALSE_PREMISE", "CAUSAL_EVIDENCE_MISSING"],
    )


def _not_available(request: ProviderRequest) -> str:
    payload = json.loads(request.user_prompt)
    unavailable_id = next(
        item["evidence_id"]
        for item in payload["evidence"]
        if item["status"] == "NOT_AVAILABLE"
    )
    return _assessment(
        [
            {
                "claim": "requested resident count",
                "support": "UNAVAILABLE",
                "evidence": [
                    {
                        "evidence_id": unavailable_id,
                        "relation": "ESTABLISHES_UNAVAILABILITY",
                    }
                ],
                "reason": "The published observation is NOT_AVAILABLE.",
            }
        ],
        reason_codes=["VALUE_NOT_AVAILABLE"],
    )


def test_supported_requested_claim_derives_answerable(
    retriever: StructuredRetriever,
) -> None:
    provider = QueueProvider([_supported])
    outcome = LlmEvidenceSufficiencyClassifierV2(retriever, provider).classify(
        "Please report Singapore's resident population for 2022."
    )

    assert outcome.decision is not None
    assert outcome.decision.classification == AnswerabilityClassification.ANSWERABLE
    assert "classification" not in provider.requests[0].output_schema["properties"]


def test_unsupported_causal_claim_can_reference_context(
    retriever: StructuredRetriever,
) -> None:
    provider = QueueProvider([_pure_causal_with_supported_premise])
    outcome = LlmEvidenceSufficiencyClassifierV2(retriever, provider).classify(
        "What reasons explain the change in Singapore's citizen population from 2017 to 2018?"
    )

    assert outcome.decision is not None
    assert outcome.decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE
    claim = outcome.decision.assessment.requested_claims[0]
    assert claim.support.value == "UNSUPPORTED"
    assert {item.relation.value for item in claim.evidence} == {"CONTEXT"}


def test_supported_premise_does_not_make_pure_causal_request_partial(
    retriever: StructuredRetriever,
) -> None:
    provider = QueueProvider([_pure_causal_with_supported_premise])
    outcome = LlmEvidenceSufficiencyClassifierV2(retriever, provider).classify(
        "What reasons explain the change in Singapore's citizen population from 2017 to 2018?"
    )

    assert outcome.decision is not None
    assert outcome.decision.assessment.premises[0].status.value == "SUPPORTED"
    assert outcome.decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE


def test_contradicted_premise_cites_contradicting_evidence(
    retriever: StructuredRetriever,
) -> None:
    provider = QueueProvider([_contradicted_premise])
    outcome = LlmEvidenceSufficiencyClassifierV2(retriever, provider).classify(
        "Why did Singapore's total population fall from 2024 to 2025?"
    )

    assert outcome.decision is not None
    premise = outcome.decision.assessment.premises[0]
    assert premise.status.value == "CONTRADICTED"
    assert {item.relation.value for item in premise.evidence} == {"CONTRADICTS"}
    assert outcome.decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE


def test_not_available_relation_is_preserved(
    retriever: StructuredRetriever,
) -> None:
    provider = QueueProvider([_not_available])
    outcome = LlmEvidenceSufficiencyClassifierV2(retriever, provider).classify(
        "Return the all-sex, all-age Census 2020 resident count for Simpang planning area."
    )

    assert outcome.decision is not None
    claim = outcome.decision.assessment.requested_claims[0]
    assert claim.support.value == "UNAVAILABLE"
    assert claim.evidence[0].relation.value == "ESTABLISHES_UNAVAILABILITY"
    assert outcome.decision.classification == AnswerabilityClassification.INSUFFICIENT_EVIDENCE


def test_compound_request_derives_partial(retriever: StructuredRetriever) -> None:
    provider = QueueProvider([_compound_partial])
    outcome = LlmEvidenceSufficiencyClassifierV2(retriever, provider).classify(
        "Report Singapore's total population in 2025, and forecast its value for 2030."
    )

    assert outcome.decision is not None
    assert outcome.decision.classification == AnswerabilityClassification.PARTIALLY_ANSWERABLE


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        (["SUPPORTED"], "ANSWERABLE"),
        (["SUPPORTED", "UNSUPPORTED"], "PARTIALLY_ANSWERABLE"),
        (["UNSUPPORTED"], "INSUFFICIENT_EVIDENCE"),
        (["UNAVAILABLE"], "INSUFFICIENT_EVIDENCE"),
        (["OUT_OF_SCOPE"], "OUT_OF_SCOPE"),
        (["OUT_OF_SCOPE", "UNSUPPORTED"], "INSUFFICIENT_EVIDENCE"),
    ],
)
def test_top_level_class_is_derived_deterministically(
    claims: list[str], expected: str
) -> None:
    claim_payloads = []
    for index, support in enumerate(claims):
        if support == "SUPPORTED":
            evidence = [{"evidence_id": f"e{index}", "relation": "SUPPORTS"}]
        elif support == "UNAVAILABLE":
            evidence = [
                {
                    "evidence_id": f"e{index}",
                    "relation": "ESTABLISHES_UNAVAILABILITY",
                }
            ]
        else:
            evidence = []
        claim_payloads.append(
            {
                "claim": f"claim {index}",
                "support": support,
                "evidence": evidence,
                "reason": "test",
            }
        )
    assessment = LlmEvidenceAssessmentV2.model_validate(
        {
            "requested_claims": claim_payloads,
            "premises": [],
            "reason_codes": ["OTHER_INSUFFICIENT_EVIDENCE"],
            "summary": "test",
        }
    )
    assert derive_answerability(assessment).value == expected


def test_derived_decision_rejects_manual_class_override() -> None:
    assessment = LlmEvidenceAssessmentV2.model_validate_json(
        _assessment(
            [
                {
                    "claim": "claim",
                    "support": "UNSUPPORTED",
                    "evidence": [],
                    "reason": "not established",
                }
            ],
            reason_codes=["OTHER_INSUFFICIENT_EVIDENCE"],
        )
    )
    with pytest.raises(ValidationError, match="deterministically derived"):
        DerivedEvidenceSufficiencyDecisionV2(
            classification=AnswerabilityClassification.ANSWERABLE,
            assessment=assessment,
        )


@pytest.mark.parametrize(
    ("model", "message"),
    [
        (
            RequestedClaimAssessmentV2,
            {
                "claim": "causal claim",
                "support": "UNSUPPORTED",
                "evidence": [{"evidence_id": "e1", "relation": "SUPPORTS"}],
                "reason": "invalid",
            },
        ),
        (
            RequestedClaimAssessmentV2,
            {
                "claim": "missing value",
                "support": "UNAVAILABLE",
                "evidence": [{"evidence_id": "e1", "relation": "CONTEXT"}],
                "reason": "invalid",
            },
        ),
        (
            PremiseAssessmentV2,
            {
                "premise": "population fell",
                "status": "CONTRADICTED",
                "evidence": [{"evidence_id": "e1", "relation": "CONTEXT"}],
                "reason": "invalid",
            },
        ),
    ],
)
def test_evidence_relation_validation(model: object, message: dict) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(message)


def test_invalid_evidence_id_exhausts_retries(retriever: StructuredRetriever) -> None:
    invalid = _assessment(
        [
            {
                "claim": "population value",
                "support": "SUPPORTED",
                "evidence": [
                    {"evidence_id": "invented:evidence", "relation": "SUPPORTS"}
                ],
                "reason": "invalid citation",
            }
        ]
    )
    provider = QueueProvider([invalid, invalid])
    outcome = LlmEvidenceSufficiencyClassifierV2(retriever, provider).classify(
        "Please report Singapore's resident population for 2022."
    )

    assert outcome.decision is None
    assert outcome.failure is not None
    assert "not supplied" in outcome.failure.message
    assert len(outcome.attempts) == 2


def test_schema_invalid_output_retries_then_succeeds(
    retriever: StructuredRetriever,
) -> None:
    provider = QueueProvider(["{}", _supported])
    outcome = LlmEvidenceSufficiencyClassifierV2(retriever, provider).classify(
        "Please report Singapore's resident population for 2022."
    )

    assert outcome.decision is not None
    assert len(outcome.attempts) == 2
    assert outcome.attempts[0].error_type == "OutputValidationError"


def test_retry_exhaustion_is_not_a_prediction(retriever: StructuredRetriever) -> None:
    error = ProviderCallError("timeout", retryable=True)
    provider = QueueProvider([error, error])
    outcome = LlmEvidenceSufficiencyClassifierV2(retriever, provider).classify(
        "Please report Singapore's resident population for 2022."
    )

    assert outcome.decision is None
    assert outcome.failure is not None
    assert len(outcome.attempts) == 2


def test_region_caveat_is_propagated_generically(retriever: StructuredRetriever) -> None:
    provider = QueueProvider([_compound_partial])
    LlmEvidenceSufficiencyClassifierV2(retriever, provider).classify(
        "What were the West Region's resident populations in 2019 and 2020, and how much did its population truly change?"
    )
    payload = json.loads(provider.requests[0].user_prompt)

    assert len(payload["evidence"]) == 2
    assert all(
        any(
            "2019 uses URA Master Plan 2014 boundaries" in note
            and "2020 onward uses Master Plan 2019 boundaries" in note
            for note in item["qualifications"]
        )
        for item in payload["evidence"]
    )


def test_v2_prompt_and_schema_are_distinct_and_zero_shot() -> None:
    assert PROMPT_VERSION_V2 == "evidence_sufficiency_zero_shot_v2"
    assert re.fullmatch(r"[0-9a-f]{64}", PROMPT_HASH_V2)
    assert "classification" not in OUTPUT_SCHEMA_V2["properties"]
    source = (PROJECT_ROOT / "app" / "llm_answerability_v2.py").read_text(
        encoding="utf-8"
    )
    assert re.search(r"\b[qt]\d{3}\b", source, flags=re.IGNORECASE) is None
    assert "few-shot" not in source.casefold()


def test_v2_classifier_has_no_evaluation_or_benchmark_dependency() -> None:
    source = (PROJECT_ROOT / "app" / "llm_answerability_v2.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert "evaluation" not in imported_roots
    assert "BenchmarkExample" not in source


def test_v2_development_evaluator_records_derived_comparison(
    retriever: StructuredRetriever,
) -> None:
    all_examples = load_benchmark(
        PROJECT_ROOT / "evaluation" / "benchmark.jsonl",
        load_evidence_index(PROCESSED_DIR),
    )
    examples = [all_examples[0], all_examples[11]]
    provider = QueueProvider([_supported, _compound_partial])
    llm = LlmEvidenceSufficiencyClassifierV2(retriever, provider)
    deterministic = DeterministicAnswerabilityClassifier(
        retriever, build_retrieval_units(PROCESSED_DIR)
    )

    report, summary = evaluate_llm_answerability_v2(
        examples, deterministic, llm
    )

    assert len(report) == 2
    assert all(row["comparison_outcome"] == "both_correct" for row in report)
    assert summary["evaluation_split"] == "development"
    assert summary["llm_v2"]["accuracy"] == 1.0
    assert summary["classification_derivation"] == "deterministic_from_requested_claims_v2"
    assert summary["cost_and_latency"]["total_provider_calls"] == 2


def test_v1_result_artifacts_are_frozen() -> None:
    expected = {
        "development_llm_answerability_report.jsonl": (
            "08de139cbaf1fbea20defb40fd8a6143c95a6996fbeb5a92caf393d251320686"
        ),
        "development_llm_answerability_summary.json": (
            "ddb91a3e245b1bcbf36f0e7c52f0937b7bdf049fbb346db96ca3fb770b68accc"
        ),
    }
    for name, digest in expected.items():
        content = (PROJECT_ROOT / "evaluation" / "results" / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest


def test_single_v2_live_result_artifacts_are_frozen() -> None:
    expected = {
        "development_llm_answerability_v2_report.jsonl": (
            "1586fe8d10ea89a94b54b920a9d62646b0144aa4b69213f0853f3710c545d535"
        ),
        "development_llm_answerability_v2_summary.json": (
            "509aaee5e45d8f45580748c33813d7fcc9fca38c831fb11764084110ff11e820"
        ),
    }
    for name, digest in expected.items():
        content = (PROJECT_ROOT / "evaluation" / "results" / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest


def test_post_run_error_analysis_does_not_change_predictions() -> None:
    analysis = json.loads(
        (
            PROJECT_ROOT
            / "evaluation"
            / "results"
            / "development_llm_answerability_v2_error_analysis.json"
        ).read_text(encoding="utf-8")
    )
    categories = {item["id"]: item["error_category"] for item in analysis["errors"]}

    assert categories["q013"] == "source_caveat_failure"
    assert categories["q016"] == "claim_decomposition_failure"
    assert categories["q017"] == "out_of_scope_failure"
    assert categories["q018"] == "evidence_relation_failure"
    assert categories["q020"] == "out_of_scope_failure"
    assert categories["q021"] == "evidence_relation_failure"
    assert categories["q022"] == "evidence_relation_failure"


def test_reserved_split_is_not_named_by_v2_runtime() -> None:
    paths = [
        PROJECT_ROOT / "app" / "llm_answerability_v2.py",
        PROJECT_ROOT / "scripts" / "evaluate_llm_answerability_v2.py",
    ]
    forbidden_split_name = "held" + "out"
    for path in paths:
        if path.exists():
            assert forbidden_split_name not in path.read_text(encoding="utf-8").casefold()
