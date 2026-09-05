# Population data boundary

This directory is a versioned snapshot of three aggregate datasets published by the Singapore Department of Statistics (SingStat) through data.gov.sg. It is the complete knowledge boundary for the next experimental stages: a question is not answerable merely because SingStat may publish the answer elsewhere.

The raw API responses and their SHA-256 checksums are retained in `raw/`. Deterministic normalized JSONL is stored in `processed/`. The retrieval timestamp in each processed record comes from `raw/manifest.json`; rerunning transformation against the same raw files and manifest produces byte-identical output.

## Selected corpus

| Source | Publisher | Coverage | Granularity | Update frequency | Format | Licence | Used for |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [Indicators On Population, Annual](https://data.gov.sg/datasets/d_3d227e5d9fdec73f3bcadce671c333a6/view) | Singapore Department of Statistics | 1950–2025, with coverage varying by series | Singapore, national | Annual | data.gov.sg API JSON; normalized JSONL | [Singapore Open Data Licence 1.0](https://data.gov.sg/open-data-licence) | Long trends; population types; growth, density, median age, support/dependency ratios, natural increase |
| [Singapore Residents By Planning Region, Age Group And Sex, End June, Annual](https://data.gov.sg/datasets/d_3c4dc32382bdc23189428af2126dd188/view) | Singapore Department of Statistics | 2019–2025 | Five URA planning regions | Annual | data.gov.sg API JSON; normalized JSONL | Singapore Open Data Licence 1.0 | Regional trends and age/sex comparisons |
| [Resident Population by Planning Area/Subzone of Residence, Age Group and Sex (Census of Population 2020)](https://data.gov.sg/datasets/d_d95ae740c0f8961a0b10435836660ce0/view) | Singapore Department of Statistics | Census 2020 | National, URA planning area, and subzone | Every 10 years | data.gov.sg API JSON; normalized JSONL | Singapore Open Data Licence 1.0 | Fine-grained spatial lookup, comparison, and aggregation for one census year |

All API endpoints use `https://data.gov.sg/api/action/datastore_search?resource_id={dataset_id}`. Exact IDs, expected columns, field types, catalog links, and SingStat TableBuilder links are declared in `app/sources.py` so an upstream schema change fails visibly.

## Candidate considered but not selected

[Singapore Residents By Age Group, Ethnic Group And Sex, At End June, Annual](https://data.gov.sg/datasets/d_3cf667d761b4bdc6d4d3d3aeec37dea5/view), also published annually by SingStat under the Singapore Open Data Licence, was considered. It is national data covering 1957–2025, represented as API JSON/CSV with `DataSeries`, one column per year, and `_id`; it has 375 series arranged hierarchically by age, ethnicity, and sex. Its footnote says 1970 and 1980 onward refer to residents, while other pre-1980 years refer to total population, and that data from 2003 onward exclude residents away continuously for at least 12 months at the reference period. It is aggregate and presents no direct personal-data concern. It would support carefully scoped national ethnicity/age/sex trends, but was excluded because age/sex analysis already exists in the selected regional and census tables and the additional hierarchy would enlarge this first corpus. Ethnicity questions are therefore deliberately outside the current knowledge boundary.

## Source-specific schemas and boundaries

### 1. Indicators On Population, Annual

**Schema.** Each of 29 rows has `DataSeries`, annual columns from 1950 through 2025, and `_id`. Series include total, resident, citizen, permanent-resident and non-resident population; population growth; density; sex ratio; resident/citizen median age; old-age support and dependency ratios under published age definitions; resident natural increase; and rate of natural increase. The API uses the literal marker `na` where a value is not available. The snapshot contains 578 such observations, all retained as `NOT_AVAILABLE`.

**Can support:**

- Direct lookup: “What was Singapore's resident population in 2024?”
- Comparison: “Was the permanent-resident population higher in 2024 or 2025?”
- Trend calculation: “How did the median age of residents change from 2010 to 2020?”
- Arithmetic using published observations, provided every required year/series is available.

**Cannot support:**

- Any planning-region, planning-area, or subzone value.
- Population counts by a particular age band or ethnicity.
- Causal claims such as why population growth changed.
- Forecasts beyond 2025, within-year variation, opinions, or unpublished definitions.
- A value represented by `na`; absence must not be treated as zero.

### 2. Residents by planning region, age group and sex

**Schema.** Each of 300 rows has a hierarchical `DataSeries` label, annual columns for 2019–2025, and `_id`. Normalization makes `planning_region`, `sex`, and `age_group` explicit. Regions are Central, East, North, North-East, and West; sex is Total, Male, or Female; age is Total or a five-year band from 0–4 through 85–89, plus 90 and over.

The official footnote says values are rounded to the nearest 10 and may not add up because of rounding. It also says 2019 uses URA Master Plan 2014 boundaries while 2020–2025 use Master Plan 2019 boundaries, so comparisons across that boundary require caution. Counts refer to residents at end June and exclude, from 2003 onward, residents away from Singapore continuously for at least 12 months at the reference period.

**Can support:**

- Direct lookup: “How many female residents aged 65–69 were in the East Region in 2023?”
- Regional comparison for the same year and demographic definition.
- Trends from 2019–2025, with the 2019/2020 boundary caveat.
- Aggregation across published age bands or regions, acknowledging rounding.

**Cannot support:**

- Planning-area or subzone trends, or any period outside 2019–2025.
- Citizen/permanent-resident/non-resident breakdowns or ethnicity.
- Exact unrounded counts.
- Reasons for regional change, migration mechanisms, forecasts, or opinions.

### 3. Census 2020 planning area/subzone population

**Schema.** Each of 388 source rows identifies `Number` (national total, planning-area total, or subzone), 60 count fields crossing Total/Male/Female with Total and 19 age groups, and `_id`. Normalization retains one record per source row and makes geography explicit; each observation carries normalized sex and age dimensions. The literal `-` occurs in 7,374 cells and is retained as `NOT_AVAILABLE`, never converted to zero or discarded. Planning areas follow URA Master Plan 2019.

**Can support:**

- Direct lookup: “What was Tampines' resident population in Census 2020?”
- Comparison: “Which had more residents aged 0–4 in 2020, Tampines or Yishun?”
- Sex/age comparisons within a planning area or subzone.
- Aggregation over available published cells for the 2020 snapshot.

**Cannot support:**

- Change over time: this source has only the 2020 census snapshot.
- Causes of differences, predictions, or post-2020 values.
- Ethnicity, citizenship, permanent-residency, income, housing, health, or migration attributes.
- Any suppressed/unavailable `-` cell; its value and reason cannot be inferred.
- Geographies under a different URA master-plan definition without another source.

## Corpus-wide exclusions

The corpus contains aggregate population observations only. It does not include explanatory prose from the *Population Trends* publication, demographic microdata, names, addresses, individual records, policy documents, causal studies, forecasts, or data from unrelated government domains. Correlation, arithmetic change, or temporal ordering does not establish causality.

The annual region table and census table both describe **residents**, not necessarily total population. Definitions, reference dates, geographic boundaries, rounding, and unavailable markers must be matched before combining sources. Cross-source arithmetic is unsupported when definitions do not align.

## Licence and privacy

All selected sources are offered under the Singapore Open Data Licence 1.0. Reuse is permitted subject to attribution, a link to the licence, no implication of government endorsement, and the licence's exclusions and disclaimers. The source attribution and retrieval date are preserved in every processed record.

The selected tables contain aggregated counts and no direct identifiers or row-level personal data. Fine-grained subzone counts and small demographic groups still require careful presentation: the project must not attempt re-identification, infer protected characteristics about individuals, or claim that an unavailable cell is zero. The licence does not grant rights over personal data even if such data were encountered elsewhere.

## Reproduction

After installing the project dependencies:

```powershell
python scripts/download_data.py
```

The default command verifies and reuses cached raw files. Use `--force` only when intentionally refreshing the snapshot. A refresh may fail when fields or types change; review the upstream change and update `app/sources.py` explicitly rather than weakening validation.
