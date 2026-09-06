"""Production assembly and startup validation for frozen System C."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.answerability import DeterministicAnswerabilityClassifier
from app.computation import DeterministicComputationEngine
from app.config import Settings
from app.generation import GroundedAnswerGenerator
from app.llm_answerability import (
    EvidenceSufficiencyProvider,
    ProviderCallError,
    ProviderRequest,
    ProviderResponse,
)
from app.models import RetrievalUnit
from app.pipeline import EvidenceAwarePipeline
from app.providers.openai_responses import OpenAIResponsesProvider
from app.retrieval import RetrievalCorpusError, build_retrieval_units
from app.structured_retrieval import StructuredRetriever


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_MANIFEST_NAME = "runtime-assets.json"
FROZEN_MODEL = "gpt-4.1-mini-2025-04-14"


class RuntimeValidationError(RuntimeError):
    """Runtime files or configuration cannot reproduce the selected system."""


class MissingApiKeyError(ProviderCallError):
    """A supported route needs generation but no API key is configured."""


class MissingApiKeyProvider:
    """Allow deterministic abstentions to work without constructing a live client."""

    provider_name = "unconfigured_openai"

    def __init__(self, requested_model: str) -> None:
        self.requested_model = requested_model

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        raise MissingApiKeyError(
            "RAG_MODEL_API_KEY is not configured; generation was not attempted",
            retryable=False,
        )


@dataclass(frozen=True)
class RuntimeAssetReport:
    manifest_path: Path
    processed_dir: Path
    asset_count: int
    retrieval_unit_count: int
    asset_hashes: dict[str, str]


@dataclass(frozen=True)
class RuntimeBundle:
    pipeline: EvidenceAwarePipeline
    units: tuple[RetrievalUnit, ...]
    assets: RuntimeAssetReport
    api_key_configured: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_project_path(project_root: Path, relative_path: str) -> Path:
    root = project_root.resolve()
    candidate = (root / relative_path).resolve()
    if Path(relative_path).is_absolute() or root not in candidate.parents:
        raise RuntimeValidationError(f"invalid runtime asset path: {relative_path!r}")
    return candidate


def validate_runtime_assets(project_root: Path = PROJECT_ROOT) -> RuntimeAssetReport:
    manifest_path = project_root / ASSET_MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeValidationError(
            f"Cannot read {ASSET_MANIFEST_NAME}. Restore the packaged runtime assets."
        ) from exc
    if manifest.get("schema_version") != 1:
        raise RuntimeValidationError("Unsupported runtime asset manifest schema.")
    assets = manifest.get("required_assets")
    if not isinstance(assets, list) or len(assets) != 3:
        raise RuntimeValidationError("Runtime manifest must declare exactly three corpus assets.")

    hashes: dict[str, str] = {}
    for entry in assets:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size_bytes"}:
            raise RuntimeValidationError("Malformed runtime asset manifest entry.")
        relative_path = entry["path"]
        path = _safe_project_path(project_root, relative_path)
        if not path.is_file():
            raise RuntimeValidationError(f"Required corpus asset is missing: {relative_path}")
        if path.stat().st_size != entry["size_bytes"]:
            raise RuntimeValidationError(f"Runtime asset size changed: {relative_path}")
        actual = _sha256(path)
        if actual != entry["sha256"]:
            raise RuntimeValidationError(f"Runtime asset checksum changed: {relative_path}")
        hashes[relative_path] = actual

    processed_dir = project_root / "data" / "processed"
    try:
        units = build_retrieval_units(processed_dir)
    except RetrievalCorpusError as exc:
        raise RuntimeValidationError(f"Corpus schema validation failed: {exc}") from exc
    expected_count = manifest.get("expected_retrieval_unit_count")
    if len(units) != expected_count:
        raise RuntimeValidationError(
            f"Corpus unit count changed: expected {expected_count}, found {len(units)}"
        )
    return RuntimeAssetReport(
        manifest_path=manifest_path,
        processed_dir=processed_dir,
        asset_count=len(assets),
        retrieval_unit_count=len(units),
        asset_hashes=hashes,
    )


def validate_runtime_configuration(settings: Settings) -> None:
    if settings.model_provider.casefold() != "openai":
        raise RuntimeValidationError("The frozen prototype requires RAG_MODEL_PROVIDER=openai.")
    if settings.model_name != FROZEN_MODEL:
        raise RuntimeValidationError(
            f"The frozen prototype requires RAG_MODEL_NAME={FROZEN_MODEL}."
        )


def build_runtime(
    *,
    project_root: Path = PROJECT_ROOT,
    settings: Optional[Settings] = None,
    provider: Optional[EvidenceSufficiencyProvider] = None,
) -> RuntimeBundle:
    """Assemble the existing pipeline; no decision or generation logic is duplicated."""

    active_settings = settings or Settings()
    validate_runtime_configuration(active_settings)
    assets = validate_runtime_assets(project_root)
    units = tuple(build_retrieval_units(assets.processed_dir))
    retriever = StructuredRetriever(units)
    api_key = (
        active_settings.model_api_key.get_secret_value()
        if active_settings.model_api_key is not None
        else ""
    )
    active_provider = provider
    if active_provider is None:
        active_provider = (
            OpenAIResponsesProvider(
                api_key=api_key,
                model=active_settings.model_name,
                timeout_seconds=active_settings.model_timeout_seconds,
            )
            if api_key
            else MissingApiKeyProvider(active_settings.model_name)
        )
    pipeline = EvidenceAwarePipeline(
        DeterministicAnswerabilityClassifier(retriever, units),
        DeterministicComputationEngine(units),
        GroundedAnswerGenerator(
            active_provider,
            max_attempts=active_settings.model_max_attempts,
            max_output_tokens=active_settings.model_max_output_tokens,
        ),
        units,
    )
    LOGGER.info(
        "runtime_ready corpus_assets=%d retrieval_units=%d api_key_configured=%s",
        assets.asset_count,
        assets.retrieval_unit_count,
        bool(api_key),
    )
    return RuntimeBundle(
        pipeline=pipeline,
        units=units,
        assets=assets,
        api_key_configured=bool(api_key),
    )
