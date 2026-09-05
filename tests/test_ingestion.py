import json
from datetime import date
from pathlib import Path

import pytest

from app.ingestion import (
    MalformedRecordError,
    SourceSchemaError,
    ingest_payload,
    parse_observation,
    read_api_payload,
    validate_api_payload,
    write_jsonl,
)
from app.models import ObservationStatus
from app.sources import SourceSpec


FIXTURES = Path(__file__).parent / "fixtures"
RETRIEVED_AT = date(2026, 9, 3)


def fixture_source(name: str, transformer: str) -> SourceSpec:
    fixture = read_api_payload(FIXTURES / f"{name}.json")
    fields = tuple(
        (field["id"], field["type"])
        for field in fixture["result"]["fields"]
    )
    return SourceSpec(
        source_id=f"fixture_{name}",
        dataset_id=fixture["result"]["resource_id"],
        title=f"Fixture {name}",
        catalog_url="https://data.gov.sg/datasets/fixture/view",
        tablebuilder_url="https://tablebuilder.singstat.gov.sg/table/fixture",
        transformer=transformer,
        temporal_start="2020",
        temporal_end="2021",
        geographic_granularity="Fixture",
        update_frequency="Fixture",
        expected_fields=fields,
    )


def test_schema_validation_rejects_added_column() -> None:
    source = fixture_source("indicator", "indicator")
    payload = read_api_payload(FIXTURES / "indicator.json")
    payload["result"]["fields"].append({"id": "unexpected", "type": "text"})

    with pytest.raises(SourceSchemaError, match="upstream fields changed"):
        validate_api_payload(payload, source)


def test_schema_validation_rejects_incomplete_page() -> None:
    source = fixture_source("indicator", "indicator")
    payload = read_api_payload(FIXTURES / "indicator.json")
    payload["result"]["total"] = 2

    with pytest.raises(SourceSchemaError, match="expected all 2 rows"):
        validate_api_payload(payload, source)


def test_indicator_transformation_retains_missing_value_and_provenance() -> None:
    source = fixture_source("indicator", "indicator")
    records = ingest_payload(
        read_api_payload(FIXTURES / "indicator.json"), source, RETRIEVED_AT
    )

    assert len(records) == 1
    assert records[0].dimensions == {"series": "Example rate"}
    assert [observation.year for observation in records[0].observations] == [2020, 2021]
    assert records[0].observations[0].status is ObservationStatus.NOT_AVAILABLE
    assert records[0].observations[0].raw_value == "na"
    assert records[0].observations[1].value == 123.5
    assert records[0].provenance.retrieved_at == RETRIEVED_AT
    assert records[0].provenance.dataset_name == "Fixture indicator"


def test_malformed_numeric_value_fails_loudly() -> None:
    with pytest.raises(MalformedRecordError, match="unrecognized numeric value"):
        parse_observation(2020, "approximately 100", context="fixture")


def test_planning_region_hierarchy_is_explicit() -> None:
    source = fixture_source("planning_region", "planning_region")
    records = ingest_payload(
        read_api_payload(FIXTURES / "planning_region.json"), source, RETRIEVED_AT
    )

    assert records[0].dimensions == {
        "planning_region": "Central Region",
        "sex": "Total",
        "age_group": "Total",
    }
    assert records[1].dimensions["planning_region"] == "Central Region"
    assert records[1].dimensions["age_group"] == "0 - 4 Years"
    assert records[2].dimensions["sex"] == "Male"


def test_orphan_planning_region_age_group_is_rejected() -> None:
    source = fixture_source("planning_region", "planning_region")
    payload = read_api_payload(FIXTURES / "planning_region.json")
    payload["result"]["records"] = [payload["result"]["records"][1]]
    payload["result"]["total"] = 1

    with pytest.raises(MalformedRecordError, match="orphan age-group"):
        ingest_payload(payload, source, RETRIEVED_AT)


def test_census_transformation_retains_geographic_levels_and_missing_values() -> None:
    source = fixture_source("census", "census_geography")
    records = ingest_payload(
        read_api_payload(FIXTURES / "census.json"), source, RETRIEVED_AT
    )

    assert len(records) == 3
    national = records[0]
    planning_area = records[1]
    missing_age_value = records[1].observations[1]
    subzone = records[2]
    assert national.dimensions["geography_level"] == "national"
    assert national.observations[0].year == 2020
    assert planning_area.dimensions["planning_area"] == "Tampines"
    assert missing_age_value.status is ObservationStatus.NOT_AVAILABLE
    assert missing_age_value.dimensions == {"sex": "Male", "age_group": "0 - 4 Years"}
    assert subzone.dimensions == {
        "geography_level": "subzone",
        "planning_area": "Tampines",
        "subzone": "Simei",
    }


def test_census_subzone_before_planning_area_is_rejected() -> None:
    source = fixture_source("census", "census_geography")
    payload = read_api_payload(FIXTURES / "census.json")
    payload["result"]["records"] = [payload["result"]["records"][2]]
    payload["result"]["total"] = 1

    with pytest.raises(MalformedRecordError, match="before a planning area"):
        ingest_payload(payload, source, RETRIEVED_AT)


def test_jsonl_output_is_deterministic(tmp_path: Path) -> None:
    source = fixture_source("indicator", "indicator")
    records = ingest_payload(
        read_api_payload(FIXTURES / "indicator.json"), source, RETRIEVED_AT
    )
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"

    write_jsonl(records, first)
    write_jsonl(records, second)

    assert first.read_bytes() == second.read_bytes()
    parsed = json.loads(first.read_text(encoding="utf-8"))
    assert parsed["source_id"] == "fixture_indicator"
