import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.ingestion import ingest_payload, read_api_payload, write_jsonl
from app.models import ObservationStatus, ProcessedDemographicRecord
from app.sources import SOURCES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
EXPECTED_SOURCE_ROWS = {
    "population_indicators_annual": 29,
    "residents_by_planning_region_annual": 300,
    "resident_population_census_2020": 388,
}
EXPECTED_MISSING_OBSERVATIONS = {
    "population_indicators_annual": 578,
    "residents_by_planning_region_annual": 0,
    "resident_population_census_2020": 7374,
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((RAW_DIR / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("source", SOURCES, ids=lambda source: source.source_id)
def test_versioned_raw_snapshot_matches_manifest(source, manifest: dict) -> None:
    raw_bytes = (RAW_DIR / source.raw_filename).read_bytes()
    entry = manifest["sources"][source.source_id]

    assert len(raw_bytes) == entry["bytes"]
    assert hashlib.sha256(raw_bytes).hexdigest() == entry["sha256"]


@pytest.mark.parametrize("source", SOURCES, ids=lambda source: source.source_id)
def test_committed_processed_snapshot_is_reproducible(
    source, manifest: dict, tmp_path: Path
) -> None:
    payload = read_api_payload(RAW_DIR / source.raw_filename)
    downloaded_at = manifest["sources"][source.source_id]["downloaded_at"]
    retrieval_date = datetime.fromisoformat(downloaded_at.replace("Z", "+00:00")).date()
    records = ingest_payload(payload, source, retrieval_date)
    regenerated = tmp_path / source.processed_filename
    write_jsonl(records, regenerated)

    assert len(records) == EXPECTED_SOURCE_ROWS[source.source_id]
    assert regenerated.read_bytes() == (PROCESSED_DIR / source.processed_filename).read_bytes()


@pytest.mark.parametrize("source", SOURCES, ids=lambda source: source.source_id)
def test_processed_snapshot_validates_and_preserves_expected_missing_values(source) -> None:
    records = [
        ProcessedDemographicRecord.model_validate_json(line)
        for line in (PROCESSED_DIR / source.processed_filename)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    missing = sum(
        observation.status is ObservationStatus.NOT_AVAILABLE
        for record in records
        for observation in record.observations
    )

    assert len(records) == EXPECTED_SOURCE_ROWS[source.source_id]
    assert missing == EXPECTED_MISSING_OBSERVATIONS[source.source_id]

