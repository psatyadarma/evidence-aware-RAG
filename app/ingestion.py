"""Deterministic normalization of the reviewed data.gov.sg sources."""

from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple, Union

from app.models import (
    DocumentMetadata,
    ObservationStatus,
    PopulationObservation,
    ProcessedDemographicRecord,
    TemporalCoverage,
)
from app.sources import OPEN_DATA_LICENCE, PUBLISHER, SourceSpec


class IngestionError(ValueError):
    """Base exception for invalid source data."""


class SourceSchemaError(IngestionError):
    """The API response no longer matches the reviewed schema."""


class MalformedRecordError(IngestionError):
    """A source record cannot be normalized without guessing."""


MISSING_MARKERS = frozenset({"", "na", "n.a.", "-", "null"})
PLANNING_REGIONS = frozenset(
    {"Central Region", "East Region", "North Region", "North-East Region", "West Region"}
)
PLANNING_AREA_TOTAL = re.compile(r"^(?P<area>.+?)\s*-\s*Total$")
REGION_PARENT = re.compile(
    r"^(?P<region>.+? Region)(?: \((?P<sex>Male|Female)\))?$"
)


def read_api_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IngestionError(f"cannot read JSON source {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SourceSchemaError("API payload must be a JSON object")
    return payload


def validate_api_payload(
    payload: dict[str, Any], source: SourceSpec
) -> list[dict[str, Any]]:
    if payload.get("success") is not True:
        raise SourceSchemaError(f"{source.source_id}: API success is not true")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise SourceSchemaError(f"{source.source_id}: result must be an object")
    if result.get("resource_id") != source.dataset_id:
        raise SourceSchemaError(f"{source.source_id}: resource_id changed")

    fields = result.get("fields")
    if not isinstance(fields, list):
        raise SourceSchemaError(f"{source.source_id}: fields must be a list")
    actual_fields = tuple(
        (field.get("id"), field.get("type"))
        for field in fields
        if isinstance(field, dict)
    )
    if len(actual_fields) != len(fields) or actual_fields != source.expected_fields:
        raise SourceSchemaError(
            f"{source.source_id}: upstream fields changed; "
            f"expected {source.expected_fields!r}, got {actual_fields!r}"
        )

    records = result.get("records")
    total = result.get("total")
    limit = result.get("limit")
    if not isinstance(records, list) or not isinstance(total, int):
        raise SourceSchemaError(f"{source.source_id}: records/total have invalid types")
    if len(records) != total:
        raise SourceSchemaError(
            f"{source.source_id}: expected all {total} rows, received {len(records)}"
        )
    if isinstance(limit, int) and total > limit:
        raise SourceSchemaError(f"{source.source_id}: response was unexpectedly paginated")

    expected_keys = {name for name, _ in source.expected_fields}
    normalized: list[dict[str, Any]] = []
    record_ids: set[int] = set()
    for position, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise MalformedRecordError(
                f"{source.source_id}: record {position} is not an object"
            )
        if set(record) != expected_keys:
            raise SourceSchemaError(
                f"{source.source_id}: record {position} columns changed"
            )
        record_id = record.get("_id")
        if not isinstance(record_id, int) or isinstance(record_id, bool) or record_id < 1:
            raise MalformedRecordError(
                f"{source.source_id}: record {position} has invalid _id {record_id!r}"
            )
        if record_id in record_ids:
            raise MalformedRecordError(
                f"{source.source_id}: duplicate _id {record_id}"
            )
        record_ids.add(record_id)
        normalized.append(record)
    return sorted(normalized, key=lambda record: record["_id"])


def parse_observation(
    year: int,
    raw_value: Any,
    *,
    context: str,
    dimensions: Optional[dict[str, str]] = None,
) -> PopulationObservation:
    observation_dimensions = dimensions or {}
    if raw_value is None:
        return PopulationObservation(
            year=year,
            value=None,
            raw_value=None,
            status=ObservationStatus.NOT_AVAILABLE,
            dimensions=observation_dimensions,
        )
    if isinstance(raw_value, bool):
        raise MalformedRecordError(f"{context}: boolean is not a numeric value")

    raw_text = str(raw_value).strip()
    if raw_text.lower() in MISSING_MARKERS:
        return PopulationObservation(
            year=year,
            value=None,
            raw_value=raw_text,
            status=ObservationStatus.NOT_AVAILABLE,
            dimensions=observation_dimensions,
        )

    try:
        if re.fullmatch(r"[+-]?\d+", raw_text):
            parsed: Union[int, float] = int(raw_text)
        elif re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+)", raw_text):
            parsed = float(raw_text)
        else:
            raise ValueError
    except ValueError as exc:
        raise MalformedRecordError(
            f"{context}: unrecognized numeric value {raw_value!r}"
        ) from exc
    if isinstance(parsed, float) and not math.isfinite(parsed):
        raise MalformedRecordError(f"{context}: numeric value must be finite")
    return PopulationObservation(
        year=year,
        value=parsed,
        raw_value=raw_text,
        status=ObservationStatus.OBSERVED,
        dimensions=observation_dimensions,
    )


def _provenance(source: SourceSpec, retrieved_at: date) -> DocumentMetadata:
    return DocumentMetadata(
        source_url=source.catalog_url,
        dataset_name=source.title,
        publisher=PUBLISHER,
        licence=OPEN_DATA_LICENCE,
        retrieved_at=retrieved_at,
        temporal_coverage=TemporalCoverage(
            start=source.temporal_start,
            end=source.temporal_end,
        ),
    )


def _annual_observations(
    record: dict[str, Any], source: SourceSpec
) -> list[PopulationObservation]:
    observations: list[PopulationObservation] = []
    for field_name, _ in source.expected_fields:
        if field_name.isdigit():
            observations.append(
                parse_observation(
                    int(field_name),
                    record[field_name],
                    context=f"{source.source_id} record {record['_id']} year {field_name}",
                )
            )
    return sorted(observations, key=lambda observation: observation.year)


def transform_indicator_records(
    records: Iterable[dict[str, Any]], source: SourceSpec, retrieved_at: date
) -> list[ProcessedDemographicRecord]:
    output: list[ProcessedDemographicRecord] = []
    provenance = _provenance(source, retrieved_at)
    for record in records:
        series = record["DataSeries"]
        if not isinstance(series, str) or not series.strip():
            raise MalformedRecordError(
                f"{source.source_id} record {record['_id']}: empty DataSeries"
            )
        output.append(
            ProcessedDemographicRecord(
                source_id=source.source_id,
                record_id=record["_id"],
                dimensions={"series": series.strip()},
                observations=_annual_observations(record, source),
                provenance=provenance,
            )
        )
    return output


def _parse_region_parent(label: str, *, context: str) -> tuple[str, str]:
    match = REGION_PARENT.fullmatch(label)
    if match is None or match.group("region") not in PLANNING_REGIONS:
        raise MalformedRecordError(f"{context}: invalid planning-region parent {label!r}")
    return match.group("region"), match.group("sex") or "Total"


def transform_planning_region_records(
    records: Iterable[dict[str, Any]], source: SourceSpec, retrieved_at: date
) -> list[ProcessedDemographicRecord]:
    output: list[ProcessedDemographicRecord] = []
    provenance = _provenance(source, retrieved_at)
    current_parent: Optional[Tuple[str, str]] = None
    for record in records:
        label = record["DataSeries"]
        if not isinstance(label, str) or not label.strip():
            raise MalformedRecordError(
                f"{source.source_id} record {record['_id']}: empty DataSeries"
            )
        if label == label.lstrip():
            current_parent = _parse_region_parent(
                label,
                context=f"{source.source_id} record {record['_id']}",
            )
            region, sex = current_parent
            age_group = "Total"
        else:
            if current_parent is None:
                raise MalformedRecordError(
                    f"{source.source_id} record {record['_id']}: orphan age-group row"
                )
            region, sex = current_parent
            age_group = label.strip()
            if not age_group.endswith(("Years", "Over")):
                raise MalformedRecordError(
                    f"{source.source_id} record {record['_id']}: invalid age group {age_group!r}"
                )
        output.append(
            ProcessedDemographicRecord(
                source_id=source.source_id,
                record_id=record["_id"],
                dimensions={"planning_region": region, "sex": sex, "age_group": age_group},
                observations=_annual_observations(record, source),
                provenance=provenance,
            )
        )
    return output


def _census_dimensions(
    label: str, current_planning_area: Optional[str], *, context: str
) -> Tuple[dict[str, str], Optional[str]]:
    if label == "Total":
        return {"geography_level": "national", "geography": "Singapore"}, None
    planning_area_match = PLANNING_AREA_TOTAL.fullmatch(label)
    if planning_area_match:
        planning_area = planning_area_match.group("area").strip()
        return (
            {"geography_level": "planning_area", "planning_area": planning_area},
            planning_area,
        )
    if current_planning_area is None:
        raise MalformedRecordError(f"{context}: subzone appears before a planning area")
    return (
        {
            "geography_level": "subzone",
            "planning_area": current_planning_area,
            "subzone": label,
        },
        current_planning_area,
    )


def _census_age_group(field_suffix: str) -> str:
    if field_suffix == "Total":
        return "Total"
    if field_suffix == "90andOver":
        return "90 Years & Over"
    return f"{field_suffix.replace('_', ' - ')} Years"


def transform_census_records(
    records: Iterable[dict[str, Any]], source: SourceSpec, retrieved_at: date
) -> list[ProcessedDemographicRecord]:
    output: list[ProcessedDemographicRecord] = []
    provenance = _provenance(source, retrieved_at)
    current_planning_area: Optional[str] = None
    for record in records:
        label = record["Number"]
        if not isinstance(label, str) or not label.strip():
            raise MalformedRecordError(
                f"{source.source_id} record {record['_id']}: empty geography label"
            )
        geography_dimensions, current_planning_area = _census_dimensions(
            label.strip(),
            current_planning_area,
            context=f"{source.source_id} record {record['_id']}",
        )
        observations: list[PopulationObservation] = []
        for field_name, _ in source.expected_fields:
            if field_name in {"Number", "_id"}:
                continue
            sex_prefix, age_suffix = field_name.split("_", maxsplit=1)
            sex = {"Total": "Total", "Males": "Male", "Females": "Female"}.get(
                sex_prefix
            )
            if sex is None:
                raise SourceSchemaError(
                    f"{source.source_id}: unknown census sex prefix {sex_prefix!r}"
                )
            observation = parse_observation(
                2020,
                record[field_name],
                context=(
                    f"{source.source_id} record {record['_id']} field {field_name}"
                ),
                dimensions={
                    "sex": sex,
                    "age_group": _census_age_group(age_suffix),
                },
            )
            observations.append(observation)
        output.append(
            ProcessedDemographicRecord(
                source_id=source.source_id,
                record_id=record["_id"],
                dimensions=geography_dimensions,
                observations=observations,
                provenance=provenance,
            )
        )
    return output


def transform_records(
    records: list[dict[str, Any]], source: SourceSpec, retrieved_at: date
) -> list[ProcessedDemographicRecord]:
    if source.transformer == "indicator":
        return transform_indicator_records(records, source, retrieved_at)
    if source.transformer == "planning_region":
        return transform_planning_region_records(records, source, retrieved_at)
    if source.transformer == "census_geography":
        return transform_census_records(records, source, retrieved_at)
    raise IngestionError(f"unsupported transformer {source.transformer!r}")


def ingest_payload(
    payload: dict[str, Any], source: SourceSpec, retrieved_at: date
) -> list[ProcessedDemographicRecord]:
    records = validate_api_payload(payload, source)
    return transform_records(records, source, retrieved_at)


def write_jsonl(records: Iterable[ProcessedDemographicRecord], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            for record in records:
                serialized = record.model_dump(mode="json")
                output.write(
                    json.dumps(serialized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                )
                output.write("\n")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
