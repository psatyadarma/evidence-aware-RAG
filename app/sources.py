"""Reviewed source registry for the deliberately small population corpus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DATA_GOV_API = "https://data.gov.sg/api/action/datastore_search"
OPEN_DATA_LICENCE = "Singapore Open Data Licence 1.0"
OPEN_DATA_LICENCE_URL = "https://data.gov.sg/open-data-licence"
PUBLISHER = "Singapore Department of Statistics (SingStat)"


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    dataset_id: str
    title: str
    catalog_url: str
    tablebuilder_url: str
    transformer: Literal["indicator", "planning_region", "census_geography"]
    temporal_start: str
    temporal_end: str
    geographic_granularity: str
    update_frequency: str
    expected_fields: tuple[tuple[str, str], ...]

    @property
    def api_url(self) -> str:
        return f"{DATA_GOV_API}?resource_id={self.dataset_id}&limit=1000"

    @property
    def raw_filename(self) -> str:
        return f"{self.source_id}.json"

    @property
    def processed_filename(self) -> str:
        return f"{self.source_id}.jsonl"


def _annual_fields(
    newest: int,
    oldest: int,
    *,
    numeric_years: frozenset[int],
) -> tuple[tuple[str, str], ...]:
    fields: list[tuple[str, str]] = [("DataSeries", "text")]
    fields.extend(
        (str(year), "numeric" if year in numeric_years else "text")
        for year in range(newest, oldest - 1, -1)
    )
    fields.append(("_id", "int4"))
    return tuple(fields)


INDICATOR_NUMERIC_YEARS = frozenset(range(1990, 2026)) | {1980}
REGION_NUMERIC_YEARS = frozenset(range(2019, 2026))

CENSUS_AGE_FIELD_SUFFIXES = (
    "Total",
    "0_4",
    "5_9",
    "10_14",
    "15_19",
    "20_24",
    "25_29",
    "30_34",
    "35_39",
    "40_44",
    "45_49",
    "50_54",
    "55_59",
    "60_64",
    "65_69",
    "70_74",
    "75_79",
    "80_84",
    "85_89",
    "90andOver",
)
CENSUS_VALUE_FIELDS = tuple(
    f"{sex}_{age}"
    for sex in ("Total", "Males", "Females")
    for age in CENSUS_AGE_FIELD_SUFFIXES
)
CENSUS_FIELDS = (
    (("Number", "text"),)
    + tuple((field_name, "text") for field_name in CENSUS_VALUE_FIELDS)
    + (("_id", "int4"),)
)


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        source_id="population_indicators_annual",
        dataset_id="d_3d227e5d9fdec73f3bcadce671c333a6",
        title="Indicators On Population, Annual",
        catalog_url=(
            "https://data.gov.sg/datasets/"
            "d_3d227e5d9fdec73f3bcadce671c333a6/view"
        ),
        tablebuilder_url="https://tablebuilder.singstat.gov.sg/table/TS/M810001",
        transformer="indicator",
        temporal_start="1950",
        temporal_end="2025",
        geographic_granularity="Singapore (national)",
        update_frequency="Annual",
        expected_fields=_annual_fields(
            2025,
            1950,
            numeric_years=INDICATOR_NUMERIC_YEARS,
        ),
    ),
    SourceSpec(
        source_id="residents_by_planning_region_annual",
        dataset_id="d_3c4dc32382bdc23189428af2126dd188",
        title="Singapore Residents By Planning Region, Age Group And Sex, End June, Annual",
        catalog_url=(
            "https://data.gov.sg/datasets/"
            "d_3c4dc32382bdc23189428af2126dd188/view"
        ),
        tablebuilder_url="https://tablebuilder.singstat.gov.sg/table/TS/M810771",
        transformer="planning_region",
        temporal_start="2019",
        temporal_end="2025",
        geographic_granularity="URA planning region",
        update_frequency="Annual",
        expected_fields=_annual_fields(
            2025,
            2019,
            numeric_years=REGION_NUMERIC_YEARS,
        ),
    ),
    SourceSpec(
        source_id="resident_population_census_2020",
        dataset_id="d_d95ae740c0f8961a0b10435836660ce0",
        title=(
            "Resident Population by Planning Area/Subzone of Residence, "
            "Age Group and Sex (Census of Population 2020)"
        ),
        catalog_url=(
            "https://data.gov.sg/datasets/"
            "d_d95ae740c0f8961a0b10435836660ce0/view"
        ),
        tablebuilder_url="https://tablebuilder.singstat.gov.sg/table/CT/17560",
        transformer="census_geography",
        temporal_start="2020",
        temporal_end="2020",
        geographic_granularity="URA planning area and subzone",
        update_frequency="Every 10 years",
        expected_fields=CENSUS_FIELDS,
    ),
)

SOURCES_BY_ID = {source.source_id: source for source in SOURCES}

