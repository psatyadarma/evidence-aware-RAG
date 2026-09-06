import ast
import hashlib
import json
from pathlib import Path

import pytest

from app.config import Settings
from app.runtime import (
    FROZEN_MODEL,
    RuntimeValidationError,
    validate_runtime_assets,
    validate_runtime_configuration,
)
from app.smoke import run_smoke_check


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_startup_validates_manifest_hashes_schema_and_unit_count():
    report = validate_runtime_assets()

    assert report.asset_count == 3
    assert report.retrieval_unit_count == 27584
    assert len(report.asset_hashes) == 3


def test_startup_missing_manifest_has_useful_error(tmp_path: Path):
    with pytest.raises(RuntimeValidationError, match="Cannot read runtime-assets.json"):
        validate_runtime_assets(tmp_path)


def test_startup_rejects_model_drift():
    settings = Settings(_env_file=None, model_name="different-model", model_api_key=None)

    with pytest.raises(RuntimeValidationError, match=FROZEN_MODEL):
        validate_runtime_configuration(settings)


def test_offline_smoke_covers_selected_deterministic_stages():
    result = run_smoke_check()

    assert result["status"] == "ok"
    assert result["provider_called"] is False
    assert result["classification"] == "ANSWERABLE"
    assert result["retrieved_evidence_count"] == 1
    assert result["computed_fact_kind"] == "lookup"


def test_runtime_manifest_matches_required_assets():
    manifest = json.loads((PROJECT_ROOT / "runtime-assets.json").read_text())

    assert manifest["expected_retrieval_unit_count"] == 27584
    assert {item["path"] for item in manifest["required_assets"]} == {
        "data/processed/population_indicators_annual.jsonl",
        "data/processed/residents_by_planning_region_annual.jsonl",
        "data/processed/resident_population_census_2020.jsonl",
    }
    for item in manifest["required_assets"]:
        path = PROJECT_ROOT / item["path"]
        assert path.stat().st_size == item["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]


def test_production_runtime_has_no_benchmark_or_results_dependency():
    paths = [
        PROJECT_ROOT / "app" / "runtime.py",
        PROJECT_ROOT / "app" / "controller.py",
        PROJECT_ROOT / "app" / "ui.py",
        PROJECT_ROOT / "app" / "cli.py",
        PROJECT_ROOT / "app" / "smoke.py",
        PROJECT_ROOT / "streamlit_app.py",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8").casefold()
        assert "benchmark.jsonl" not in source
        assert "heldout_benchmark" not in source
        assert "evaluation/results" not in source
        assert "expected_answer" not in source

    runtime_tree = ast.parse((PROJECT_ROOT / "app" / "runtime.py").read_text())
    imports = {
        node.module
        for node in ast.walk(runtime_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(item == "evaluation" or item.startswith("evaluation.") for item in imports)


def test_dockerfile_packages_only_selected_runtime_assets():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert dockerfile.startswith("FROM python:3.11.13-slim-bookworm")
    assert "USER appuser" in dockerfile
    assert "EXPOSE 8501" in dockerfile
    assert "COPY data/processed ./data/processed" in dockerfile
    assert 'CMD ["python", "-m", "streamlit"' in dockerfile
    assert "RAG_MODEL_API_KEY" not in dockerfile
    assert "data/raw" in dockerignore
    assert "data/embeddings" in dockerignore
    assert "evaluation" in dockerignore
    assert "tests" in dockerignore
    assert "data/processed" not in dockerignore
