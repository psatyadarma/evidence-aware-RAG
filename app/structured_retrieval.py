"""Deterministic schema-aware retrieval for the population corpus.

The interpreter is deliberately limited to values discovered in retrieval-unit
metadata plus small, documented language normalisations.  It has no access to
ground-truth files and performs no numerical computation.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Iterable, Optional

from pydantic import Field, model_validator

from app.models import RetrievalResult, RetrievalUnit, StrictModel


STRUCTURED_RETRIEVER_NAME = "structured_schema_v1"


class StructuredQueryError(ValueError):
    """The corpus metadata cannot support deterministic interpretation."""


class MeasureStatus(str, Enum):
    RESOLVED = "resolved"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class GeographyLevel(str, Enum):
    NATIONAL = "national"
    PLANNING_REGION = "planning_region"
    PLANNING_AREA = "planning_area"
    SUBZONE = "subzone"


class AgeConstraintKind(str, Enum):
    TOTAL = "total"
    RANGE = "range"
    AT_LEAST = "at_least"
    UNDER = "under"


class StructuredOperation(str, Enum):
    LOOKUP = "lookup"
    COMPARISON = "comparison"
    DIFFERENCE = "difference"
    SUM = "sum"
    ARGMAX = "argmax"
    DESCRIPTIVE_TREND = "descriptive_trend"
    CAUSAL_EXPLANATION = "causal_explanation"
    UNSUPPORTED_UNKNOWN = "unsupported_unknown"


class AgeBand(StrictModel):
    label: str = Field(min_length=1)
    minimum_age: Optional[int] = Field(default=None, ge=0, le=130)
    maximum_age: Optional[int] = Field(default=None, ge=0, le=130)
    is_total: bool = False

    @model_validator(mode="after")
    def bounds_are_consistent(self) -> "AgeBand":
        if self.is_total:
            if self.minimum_age is not None or self.maximum_age is not None:
                raise ValueError("a total age band cannot have numeric bounds")
        elif self.minimum_age is None:
            raise ValueError("a non-total age band requires a minimum age")
        elif self.maximum_age is not None and self.maximum_age < self.minimum_age:
            raise ValueError("age-band maximum cannot precede its minimum")
        return self


class AgeConstraint(StrictModel):
    kind: AgeConstraintKind
    minimum_age: Optional[int] = Field(default=None, ge=0, le=130)
    maximum_age: Optional[int] = Field(default=None, ge=0, le=130)

    @model_validator(mode="after")
    def bounds_match_kind(self) -> "AgeConstraint":
        if self.kind == AgeConstraintKind.TOTAL:
            if self.minimum_age is not None or self.maximum_age is not None:
                raise ValueError("total age constraints cannot have bounds")
        elif self.kind in {AgeConstraintKind.AT_LEAST, AgeConstraintKind.UNDER}:
            if self.minimum_age is None or self.maximum_age is not None:
                raise ValueError(f"{self.kind.value} requires one threshold")
        elif self.kind == AgeConstraintKind.RANGE:
            if self.minimum_age is None or self.maximum_age is None:
                raise ValueError("range requires minimum and maximum")
            if self.maximum_age < self.minimum_age:
                raise ValueError("age maximum cannot precede minimum")
        return self


class GeographyCandidate(StrictModel):
    level: GeographyLevel
    entity: str = Field(min_length=1)
    planning_area: Optional[str] = Field(default=None, min_length=1)


class StructuredQuery(StrictModel):
    question: str = Field(min_length=1)
    measure_status: MeasureStatus
    requested_measure: Optional[str] = Field(default=None, min_length=1)
    unsupported_attribute: Optional[str] = Field(default=None, min_length=1)
    years: list[int] = Field(default_factory=list)
    geography_level: Optional[GeographyLevel] = None
    geographies: list[GeographyCandidate] = Field(default_factory=list)
    geography_ambiguous: bool = False
    all_geographies_at_level: bool = False
    age_constraint: Optional[AgeConstraint] = None
    resolved_age_groups: list[str] = Field(default_factory=list)
    sexes: list[str] = Field(default_factory=list)
    operation: StructuredOperation
    evidence_operation: StructuredOperation
    defaulted_age_to_total: bool = False
    defaulted_sex_to_total: bool = False
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def semantics_are_consistent(self) -> "StructuredQuery":
        if len(self.years) != len(set(self.years)):
            raise ValueError("years must be unique")
        if self.measure_status == MeasureStatus.RESOLVED:
            if self.requested_measure is None or self.unsupported_attribute is not None:
                raise ValueError("resolved measure requires only requested_measure")
        elif self.measure_status == MeasureStatus.UNSUPPORTED:
            if self.unsupported_attribute is None or self.requested_measure is not None:
                raise ValueError("unsupported measure requires only unsupported_attribute")
        elif self.requested_measure is not None or self.unsupported_attribute is not None:
            raise ValueError("unknown measure cannot contain a resolved attribute")
        if self.geography_ambiguous and self.geography_level is not None:
            raise ValueError("ambiguous geography cannot have one resolved level")
        if self.all_geographies_at_level and self.geography_level is None:
            raise ValueError("all-geographies selection requires a resolved level")
        if self.defaulted_age_to_total:
            if self.age_constraint is None or self.age_constraint.kind != AgeConstraintKind.TOTAL:
                raise ValueError("defaulted age must resolve to total")
        if self.defaulted_sex_to_total and self.sexes != ["Total"]:
            raise ValueError("defaulted sex must resolve to total")
        if len(self.sexes) != len(set(self.sexes)):
            raise ValueError("sex selections must be unique")
        return self


class StructuredRetrievalPlan(StrictModel):
    query: StructuredQuery
    diagnostics: list[str] = Field(default_factory=list)
    results: list[RetrievalResult] = Field(default_factory=list)


def _normalise_words(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = re.sub(r"['’]s\b", "", value)
    value = value.replace("&", " and ")
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def parse_published_age_band(label: str) -> AgeBand:
    """Parse the actual corpus labels as closed ranges or open upper ranges."""

    if label.casefold() == "total":
        return AgeBand(label=label, is_total=True)
    range_match = re.fullmatch(
        r"(\d{1,3})\s*[-–—]\s*(\d{1,3})(?:\s+years?)?",
        label.strip(),
        flags=re.IGNORECASE,
    )
    if range_match:
        return AgeBand(
            label=label,
            minimum_age=int(range_match.group(1)),
            maximum_age=int(range_match.group(2)),
        )
    open_match = re.fullmatch(
        r"(\d{1,3})(?:\s+years?)?\s*(?:&|and)?\s*(?:over|older|\+)",
        label.strip(),
        flags=re.IGNORECASE,
    )
    if open_match:
        return AgeBand(label=label, minimum_age=int(open_match.group(1)))
    raise StructuredQueryError(f"unrecognised published age band {label!r}")


class CorpusRegistry:
    """Valid domain values derived entirely from retrieval-unit metadata."""

    def __init__(self, units: Iterable[RetrievalUnit]) -> None:
        materialised = tuple(units)
        if not materialised:
            raise StructuredQueryError("structured retrieval requires a non-empty corpus")
        self.series = tuple(
            sorted({unit.dimensions["series"] for unit in materialised if "series" in unit.dimensions})
        )
        labels = sorted(
            {unit.dimensions["age_group"] for unit in materialised if "age_group" in unit.dimensions},
            key=lambda value: (value.casefold() != "total", value.casefold()),
        )
        self.age_bands = tuple(parse_published_age_band(label) for label in labels)
        candidates: dict[tuple[GeographyLevel, str, Optional[str]], GeographyCandidate] = {}
        for unit in materialised:
            dimensions = unit.dimensions
            if "planning_region" in dimensions:
                candidate = GeographyCandidate(
                    level=GeographyLevel.PLANNING_REGION,
                    entity=dimensions["planning_region"],
                )
                candidates[(candidate.level, candidate.entity, None)] = candidate
            level = dimensions.get("geography_level")
            if level == GeographyLevel.PLANNING_AREA.value:
                candidate = GeographyCandidate(
                    level=GeographyLevel.PLANNING_AREA,
                    entity=dimensions["planning_area"],
                )
                candidates[(candidate.level, candidate.entity, None)] = candidate
            elif level == GeographyLevel.SUBZONE.value:
                candidate = GeographyCandidate(
                    level=GeographyLevel.SUBZONE,
                    entity=dimensions["subzone"],
                    planning_area=dimensions["planning_area"],
                )
                candidates[(candidate.level, candidate.entity, candidate.planning_area)] = candidate
        candidates[(GeographyLevel.NATIONAL, "Singapore", None)] = GeographyCandidate(
            level=GeographyLevel.NATIONAL,
            entity="Singapore",
        )
        self.geographies = tuple(
            sorted(candidates.values(), key=lambda item: (item.entity.casefold(), item.level.value, item.planning_area or ""))
        )

    def resolve_age_groups(self, constraint: AgeConstraint) -> list[str]:
        if constraint.kind == AgeConstraintKind.TOTAL:
            return [band.label for band in self.age_bands if band.is_total]
        resolved: list[AgeBand] = []
        for band in self.age_bands:
            if band.is_total or band.minimum_age is None:
                continue
            if constraint.kind == AgeConstraintKind.AT_LEAST:
                if band.minimum_age >= constraint.minimum_age:
                    resolved.append(band)
            elif constraint.kind == AgeConstraintKind.UNDER:
                if band.maximum_age is not None and band.maximum_age < constraint.minimum_age:
                    resolved.append(band)
            elif constraint.kind == AgeConstraintKind.RANGE:
                if (
                    band.maximum_age is not None
                    and band.minimum_age >= constraint.minimum_age
                    and band.maximum_age <= constraint.maximum_age
                ):
                    resolved.append(band)
        return [
            band.label
            for band in sorted(resolved, key=lambda item: (item.minimum_age or 0, item.maximum_age or 10_000))
        ]

    def age_resolution_is_exact(
        self, constraint: AgeConstraint, resolved_labels: list[str]
    ) -> bool:
        if constraint.kind == AgeConstraintKind.TOTAL:
            return resolved_labels == ["Total"]
        by_label = {band.label: band for band in self.age_bands}
        bands = [by_label[label] for label in resolved_labels]
        if not bands:
            return False
        for previous, current in zip(bands, bands[1:]):
            if previous.maximum_age is None or current.minimum_age != previous.maximum_age + 1:
                return False
        if constraint.kind == AgeConstraintKind.AT_LEAST:
            return (
                bands[0].minimum_age == constraint.minimum_age
                and bands[-1].maximum_age is None
            )
        if constraint.kind == AgeConstraintKind.UNDER:
            return (
                bands[0].minimum_age == 0
                and bands[-1].maximum_age == constraint.minimum_age - 1
            )
        return (
            bands[0].minimum_age == constraint.minimum_age
            and bands[-1].maximum_age == constraint.maximum_age
        )


UNSUPPORTED_ATTRIBUTES = (
    ("household income", "household income"),
    ("income", "income"),
    ("school quality", "school quality"),
    ("schools", "schools"),
    ("school", "school"),
    ("education", "education"),
    ("health", "health"),
)


class DeterministicQueryInterpreter:
    def __init__(self, registry: CorpusRegistry) -> None:
        self.registry = registry

    def _measure(self, words: str) -> tuple[MeasureStatus, Optional[str], Optional[str]]:
        for phrase, attribute in UNSUPPORTED_ATTRIBUTES:
            if _contains_phrase(words, phrase):
                return MeasureStatus.UNSUPPORTED, None, attribute
        for series in sorted(self.registry.series, key=lambda item: len(_normalise_words(item)), reverse=True):
            if _contains_phrase(words, _normalise_words(series)):
                return MeasureStatus.RESOLVED, series, None
        aliases = (
            ("permanent residents", "Permanent Resident Population"),
            ("permanent resident", "Permanent Resident Population"),
            ("non residents", "Non-Resident Population"),
            ("non resident", "Non-Resident Population"),
            ("citizens", "Citizen Population"),
            ("citizen population", "Citizen Population"),
            ("residents", "Resident Population"),
            ("resident", "Resident Population"),
        )
        for phrase, series in aliases:
            if _contains_phrase(words, phrase) and series in self.registry.series:
                return MeasureStatus.RESOLVED, series, None
        if _contains_phrase(words, "population") and "Total Population" in self.registry.series:
            return MeasureStatus.RESOLVED, "Total Population", None
        return MeasureStatus.UNKNOWN, None, None

    @staticmethod
    def _years(question: str) -> list[int]:
        years: list[int] = []
        for match in re.finditer(r"(?<!\d)(?:19|20)\d{2}(?!\d)", question):
            year = int(match.group())
            if year not in years:
                years.append(year)
        words = _normalise_words(question)
        if len(years) == 2 and any(
            _contains_phrase(words, phrase) for phrase in ("each year", "every year", "annually")
        ):
            lower, upper = sorted(years)
            return list(range(lower, upper + 1))
        return years

    def _geography(
        self, words: str
    ) -> tuple[Optional[GeographyLevel], list[GeographyCandidate], bool, bool, list[str]]:
        explicit: Optional[GeographyLevel] = None
        if _contains_phrase(words, "subzone"):
            explicit = GeographyLevel.SUBZONE
        elif _contains_phrase(words, "planning area"):
            explicit = GeographyLevel.PLANNING_AREA
        elif _contains_phrase(words, "planning region") or _contains_phrase(words, "region"):
            explicit = GeographyLevel.PLANNING_REGION

        by_name: dict[str, list[GeographyCandidate]] = {}
        for candidate in self.registry.geographies:
            by_name.setdefault(_normalise_words(candidate.entity), []).append(candidate)
        matches: list[tuple[int, int, list[GeographyCandidate]]] = []
        occupied: list[tuple[int, int]] = []
        for name in sorted(by_name, key=lambda value: (-len(value), value)):
            for match in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", words):
                span = match.span()
                if any(span[0] < end and start < span[1] for start, end in occupied):
                    continue
                matches.append((span[0], span[1], by_name[name]))
                occupied.append(span)
        matches.sort(key=lambda item: item[0])
        found = [candidate for _, _, group in matches for candidate in group]
        if explicit is not None and explicit != GeographyLevel.NATIONAL:
            found = [candidate for candidate in found if candidate.level == explicit]
        diagnostics: list[str] = []
        non_country_match = any(
            candidate.level != GeographyLevel.NATIONAL
            for _, _, group in matches
            for candidate in group
        )
        if explicit is not None and non_country_match and not found:
            diagnostics.append("geography_level_name_mismatch")
        unique: list[GeographyCandidate] = []
        seen: set[tuple[str, str, Optional[str]]] = set()
        for candidate in found:
            key = (candidate.level.value, candidate.entity, candidate.planning_area)
            if key not in seen:
                unique.append(candidate)
                seen.add(key)
        levels = {candidate.level for candidate in unique}
        ambiguous = len(levels) > 1
        if ambiguous:
            diagnostics.append("ambiguous_geography_level")
            level = None
        elif levels:
            level = next(iter(levels))
        else:
            level = explicit
        all_at_level = (
            level is not None
            and level != GeographyLevel.NATIONAL
            and not unique
            and not non_country_match
        )
        if all_at_level:
            diagnostics.append("all_geographies_at_requested_level")
        return level, unique, ambiguous, all_at_level, diagnostics

    @staticmethod
    def _age(question: str) -> Optional[AgeConstraint]:
        text = unicodedata.normalize("NFKC", question).casefold()
        match = re.search(r"(?:aged?\s+)?at\s+least\s+(\d{1,3})", text)
        if match:
            return AgeConstraint(kind=AgeConstraintKind.AT_LEAST, minimum_age=int(match.group(1)))
        match = re.search(
            r"(?:aged?\s+)?(\d{1,3})\s*(?:years?\s*)?(?:and\s+over|or\s+older|\+)", text
        )
        if match:
            return AgeConstraint(kind=AgeConstraintKind.AT_LEAST, minimum_age=int(match.group(1)))
        match = re.search(r"(?:under|younger\s+than)\s+(\d{1,3})", text)
        if match:
            return AgeConstraint(kind=AgeConstraintKind.UNDER, minimum_age=int(match.group(1)))
        match = re.search(
            r"(?:aged?|ages?|age\s+group)?\s*(\d{1,3})\s*[-–—]\s*(\d{1,3})(?!\d)", text
        )
        if match:
            return AgeConstraint(
                kind=AgeConstraintKind.RANGE,
                minimum_age=int(match.group(1)),
                maximum_age=int(match.group(2)),
            )
        match = re.search(r"(?:aged?|age)\s+(\d{1,3})(?!\d)", text)
        if match:
            age = int(match.group(1))
            return AgeConstraint(kind=AgeConstraintKind.RANGE, minimum_age=age, maximum_age=age)
        return None

    @staticmethod
    def _sexes(words: str) -> list[str]:
        if any(_contains_phrase(words, phrase) for phrase in ("both sexes", "all sexes")):
            return ["Total"]
        mentions: list[tuple[int, str]] = []
        for pattern, value in (
            (r"\b(?:female|females|woman|women|girl|girls)\b", "Female"),
            (r"\b(?:male|males|man|men|boy|boys)\b", "Male"),
        ):
            for match in re.finditer(pattern, words):
                mentions.append((match.start(), value))
        values: list[str] = []
        for _, value in sorted(mentions):
            if value not in values:
                values.append(value)
        return values

    @staticmethod
    def _operations(
        words: str,
        years: list[int],
        geographies: list[GeographyCandidate],
        sexes: list[str],
        resolved_age_groups: list[str],
        measure_status: MeasureStatus,
    ) -> tuple[StructuredOperation, StructuredOperation]:
        if measure_status != MeasureStatus.RESOLVED:
            evidence_operation = StructuredOperation.UNSUPPORTED_UNKNOWN
        elif any(_contains_phrase(words, phrase) for phrase in ("largest", "highest", "most")):
            evidence_operation = StructuredOperation.ARGMAX
        elif any(
            _contains_phrase(words, phrase)
            for phrase in ("how did", "trend", "grown since", "growth since")
        ):
            evidence_operation = StructuredOperation.DESCRIPTIVE_TREND
        elif any(
            _contains_phrase(words, phrase)
            for phrase in ("how many more", "how much", "difference", "change between", "change from")
        ):
            evidence_operation = StructuredOperation.DIFFERENCE
        elif len({item.entity for item in geographies}) > 1 or len(sexes) > 1 or len(years) > 1:
            evidence_operation = StructuredOperation.COMPARISON
        elif len(resolved_age_groups) > 1:
            evidence_operation = StructuredOperation.SUM
        else:
            evidence_operation = StructuredOperation.LOOKUP
        causal = any(
            re.search(rf"\b{term}\b", words) is not None
            for term in ("why", "cause", "caused", "reason", "reasons")
        )
        operation = StructuredOperation.CAUSAL_EXPLANATION if causal else evidence_operation
        return operation, evidence_operation

    def interpret(self, question: str) -> StructuredQuery:
        words = _normalise_words(question)
        measure_status, measure, unsupported = self._measure(words)
        years = self._years(question)
        level, geographies, ambiguous, all_at_level, diagnostics = self._geography(words)
        age_constraint = self._age(question)
        sexes = self._sexes(words)
        non_national_count = measure == "Resident Population" and (
            level in {
                GeographyLevel.PLANNING_REGION,
                GeographyLevel.PLANNING_AREA,
                GeographyLevel.SUBZONE,
            }
            or any(item.level != GeographyLevel.NATIONAL for item in geographies)
        )
        defaulted_age = False
        defaulted_sex = False
        resolved_age_groups: list[str] = []
        if non_national_count:
            if age_constraint is None:
                age_constraint = AgeConstraint(kind=AgeConstraintKind.TOTAL)
                defaulted_age = True
            resolved_age_groups = self.registry.resolve_age_groups(age_constraint)
            if not self.registry.age_resolution_is_exact(
                age_constraint, resolved_age_groups
            ):
                diagnostics.append("age_constraint_not_exactly_representable")
            if not sexes:
                sexes = ["Total"]
                defaulted_sex = True
        operation, evidence_operation = self._operations(
            words, years, geographies, sexes, resolved_age_groups, measure_status
        )
        if not years:
            diagnostics.append("year_not_specified")
        if measure_status == MeasureStatus.UNSUPPORTED:
            diagnostics.append("unsupported_attribute")
        elif measure_status == MeasureStatus.UNKNOWN:
            diagnostics.append("measure_not_resolved")
        if operation == StructuredOperation.CAUSAL_EXPLANATION:
            diagnostics.append("causal_explanation_not_supported_by_descriptive_observations")
        return StructuredQuery(
            question=question,
            measure_status=measure_status,
            requested_measure=measure,
            unsupported_attribute=unsupported,
            years=years,
            geography_level=level,
            geographies=geographies,
            geography_ambiguous=ambiguous,
            all_geographies_at_level=all_at_level,
            age_constraint=age_constraint,
            resolved_age_groups=resolved_age_groups,
            sexes=sexes,
            operation=operation,
            evidence_operation=evidence_operation,
            defaulted_age_to_total=defaulted_age,
            defaulted_sex_to_total=defaulted_sex,
            diagnostics=diagnostics,
        )


class StructuredRetriever:
    """Exact filtering plus a stable query-semantic ordering policy."""

    def __init__(self, units: Iterable[RetrievalUnit]) -> None:
        self._units = tuple(sorted(units, key=lambda item: item.evidence_id))
        self.registry = CorpusRegistry(self._units)
        self.interpreter = DeterministicQueryInterpreter(self.registry)

    @property
    def corpus_size(self) -> int:
        return len(self._units)

    @staticmethod
    def _candidate_matches(unit: RetrievalUnit, candidate: GeographyCandidate) -> bool:
        dimensions = unit.dimensions
        if candidate.level == GeographyLevel.NATIONAL:
            return "series" in dimensions
        if candidate.level == GeographyLevel.PLANNING_REGION:
            return dimensions.get("planning_region") == candidate.entity
        if candidate.level == GeographyLevel.PLANNING_AREA:
            return (
                dimensions.get("geography_level") == GeographyLevel.PLANNING_AREA.value
                and dimensions.get("planning_area") == candidate.entity
            )
        return (
            dimensions.get("geography_level") == GeographyLevel.SUBZONE.value
            and dimensions.get("subzone") == candidate.entity
            and (
                candidate.planning_area is None
                or dimensions.get("planning_area") == candidate.planning_area
            )
        )

    @staticmethod
    def _level_matches(unit: RetrievalUnit, level: GeographyLevel) -> bool:
        dimensions = unit.dimensions
        if level == GeographyLevel.NATIONAL:
            return "series" in dimensions
        if level == GeographyLevel.PLANNING_REGION:
            return "planning_region" in dimensions
        return dimensions.get("geography_level") == level.value

    def plan(self, question: str) -> StructuredRetrievalPlan:
        query = self.interpreter.interpret(question)
        diagnostics = list(query.diagnostics)
        if query.measure_status != MeasureStatus.RESOLVED:
            return StructuredRetrievalPlan(query=query, diagnostics=diagnostics)
        if not query.years:
            return StructuredRetrievalPlan(query=query, diagnostics=diagnostics)
        if "geography_level_name_mismatch" in diagnostics:
            return StructuredRetrievalPlan(query=query, diagnostics=diagnostics)

        selected: list[RetrievalUnit] = []
        for unit in self._units:
            if unit.year not in query.years:
                continue
            if query.geographies:
                if not any(self._candidate_matches(unit, candidate) for candidate in query.geographies):
                    continue
            elif query.geography_level is not None:
                if not self._level_matches(unit, query.geography_level):
                    continue
            elif not self._level_matches(unit, GeographyLevel.NATIONAL):
                continue

            is_national = "series" in unit.dimensions
            if is_national:
                if unit.dimensions.get("series") != query.requested_measure:
                    continue
            elif query.requested_measure != "Resident Population":
                continue
            if not is_national:
                if query.resolved_age_groups and unit.dimensions.get("age_group") not in query.resolved_age_groups:
                    continue
                if query.sexes and unit.dimensions.get("sex") not in query.sexes:
                    continue
            selected.append(unit)

        if not selected:
            diagnostics.append("no_exact_schema_match")

        year_order = {year: index for index, year in enumerate(query.years)}
        geography_order = {
            (candidate.level.value, candidate.entity, candidate.planning_area): index
            for index, candidate in enumerate(query.geographies)
        }
        age_order = {label: index for index, label in enumerate(query.resolved_age_groups)}
        sex_order = {value: index for index, value in enumerate(query.sexes)}

        def order_key(unit: RetrievalUnit) -> tuple:
            dimensions = unit.dimensions
            candidate_indexes = [
                geography_order[(candidate.level.value, candidate.entity, candidate.planning_area)]
                for candidate in query.geographies
                if self._candidate_matches(unit, candidate)
            ]
            geographic_name = (
                dimensions.get("planning_region")
                or dimensions.get("planning_area")
                or dimensions.get("subzone")
                or dimensions.get("geography")
                or ""
            )
            return (
                year_order.get(unit.year, len(year_order)),
                min(candidate_indexes) if candidate_indexes else len(geography_order),
                geographic_name.casefold(),
                age_order.get(dimensions.get("age_group", ""), len(age_order)),
                sex_order.get(dimensions.get("sex", ""), len(sex_order)),
                unit.evidence_id,
            )

        ordered = sorted(selected, key=order_key)
        results = [
            RetrievalResult(
                evidence=unit,
                score=1.0,
                rank=index,
                retriever=STRUCTURED_RETRIEVER_NAME,
            )
            for index, unit in enumerate(ordered, start=1)
        ]
        return StructuredRetrievalPlan(query=query, diagnostics=diagnostics, results=results)

    def retrieve_all(self, question: str) -> list[RetrievalResult]:
        return self.plan(question).results

    def retrieve(self, question: str, k: int) -> list[RetrievalResult]:
        if k <= 0:
            raise ValueError("k must be positive")
        return self.retrieve_all(question)[:k]
