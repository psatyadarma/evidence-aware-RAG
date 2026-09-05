"""Deterministic evidence-sufficiency decisions over structured retrieval."""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Iterable, Optional

from pydantic import Field, model_validator

from app.models import (
    AnswerabilityClassification,
    ObservationStatus,
    RetrievalResult,
    RetrievalUnit,
    StrictModel,
)
from app.structured_retrieval import (
    GeographyLevel,
    MeasureStatus,
    StructuredOperation,
    StructuredQuery,
    StructuredRetriever,
)


class ClaimKind(str, Enum):
    DESCRIPTIVE = "descriptive"
    CAUSAL = "causal"
    FORECAST = "forecast"
    OUT_OF_DOMAIN = "out_of_domain"
    UNDERLYING_CHANGE = "underlying_change"


class ClaimSupport(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNAVAILABLE = "UNAVAILABLE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    AMBIGUOUS = "AMBIGUOUS"


class DecisionReason(str, Enum):
    EVIDENCE_COMPLETE = "EVIDENCE_COMPLETE"
    UNSUPPORTED_ATTRIBUTE = "UNSUPPORTED_ATTRIBUTE"
    REQUESTED_YEAR_UNAVAILABLE = "REQUESTED_YEAR_UNAVAILABLE"
    REQUESTED_GEOGRAPHY_UNAVAILABLE = "REQUESTED_GEOGRAPHY_UNAVAILABLE"
    VALUE_NOT_AVAILABLE = "VALUE_NOT_AVAILABLE"
    CAUSAL_EVIDENCE_MISSING = "CAUSAL_EVIDENCE_MISSING"
    PREDICTION_REQUESTED = "PREDICTION_REQUESTED"
    FALSE_PREMISE = "FALSE_PREMISE"
    INCOMPATIBLE_COMPARISON = "INCOMPATIBLE_COMPARISON"
    PARTIAL_SUPPORT = "PARTIAL_SUPPORT"
    AMBIGUOUS_GEOGRAPHY = "AMBIGUOUS_GEOGRAPHY"
    BOUNDARY_DEFINITION_CHANGE = "BOUNDARY_DEFINITION_CHANGE"
    MISSING_REQUIRED_OPERANDS = "MISSING_REQUIRED_OPERANDS"
    QUERY_NOT_RESOLVED = "QUERY_NOT_RESOLVED"


class AnswerabilityClaim(StrictModel):
    kind: ClaimKind
    description: str = Field(min_length=1)
    support: ClaimSupport
    evidence_ids: list[str] = Field(default_factory=list)
    reasons: list[DecisionReason] = Field(default_factory=list)

    @model_validator(mode="after")
    def supported_claim_has_evidence(self) -> "AnswerabilityClaim":
        if self.support == ClaimSupport.SUPPORTED and not self.evidence_ids:
            raise ValueError("supported claims require evidence")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("claim evidence IDs must be unique")
        return self


class PremiseCheck(StrictModel):
    relation: str = Field(min_length=1)
    actual_relation: str = Field(min_length=1)
    contradicted: bool
    evidence_ids: list[str] = Field(min_length=2)


class DeterministicAnswerabilityDecision(StrictModel):
    classification: AnswerabilityClassification
    claims: list[AnswerabilityClaim] = Field(min_length=1)
    reasons: list[DecisionReason] = Field(min_length=1)
    retrieved_evidence_ids: list[str] = Field(default_factory=list)
    structured_query: StructuredQuery
    premise_check: Optional[PremiseCheck] = None

    @model_validator(mode="after")
    def classification_matches_claims(self) -> "DeterministicAnswerabilityDecision":
        supported = sum(claim.support == ClaimSupport.SUPPORTED for claim in self.claims)
        if self.classification == AnswerabilityClassification.ANSWERABLE:
            if supported != len(self.claims):
                raise ValueError("ANSWERABLE requires every claim to be supported")
        elif self.classification == AnswerabilityClassification.PARTIALLY_ANSWERABLE:
            if supported == 0 or supported == len(self.claims):
                raise ValueError("PARTIALLY_ANSWERABLE requires mixed claim support")
        elif self.classification == AnswerabilityClassification.OUT_OF_SCOPE:
            if any(claim.support != ClaimSupport.OUT_OF_SCOPE for claim in self.claims):
                raise ValueError("OUT_OF_SCOPE requires only out-of-scope claims")
        elif supported:
            raise ValueError("INSUFFICIENT_EVIDENCE cannot contain supported claims")
        if len(self.retrieved_evidence_ids) != len(set(self.retrieved_evidence_ids)):
            raise ValueError("retrieved evidence IDs must be unique")
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("decision reasons must be unique")
        return self


OUT_OF_DOMAIN_TERMS = (
    "household income",
    "income",
    "school quality",
    "schools",
    "school",
    "education",
    "health",
    "housing price",
    "resale price",
    "hdb price",
    "employment",
    "unemployment",
    "ethnicity",
    "life expectancy",
    "household size",
    "election",
    "voted",
    "opinion",
)
FORECAST_TERMS = (
    "forecast",
    "predict",
    "prediction",
    "project",
    "projection",
    "estimate for",
)
UNDERLYING_CHANGE_TERMS = (
    "truly change",
    "true change",
    "actual change",
    "underlying change",
    "directly comparable",
)


def _normalise(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = re.sub(r"['’]s\b", "", value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _contains(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _unique(values: Iterable[DecisionReason]) -> list[DecisionReason]:
    return list(dict.fromkeys(values))


class DeterministicAnswerabilityClassifier:
    """Apply transparent claim and evidence-completeness rules."""

    def __init__(
        self,
        structured_retriever: StructuredRetriever,
        corpus_units: Iterable[RetrievalUnit],
    ) -> None:
        self.retriever = structured_retriever
        self._units = tuple(corpus_units)
        self._years_by_level: dict[GeographyLevel, set[int]] = {
            level: set() for level in GeographyLevel
        }
        self._years_by_series: dict[str, set[int]] = {}
        for unit in self._units:
            dimensions = unit.dimensions
            if "series" in dimensions:
                self._years_by_level[GeographyLevel.NATIONAL].add(unit.year)
                self._years_by_series.setdefault(dimensions["series"], set()).add(unit.year)
            elif "planning_region" in dimensions:
                self._years_by_level[GeographyLevel.PLANNING_REGION].add(unit.year)
            elif dimensions.get("geography_level") == GeographyLevel.PLANNING_AREA.value:
                self._years_by_level[GeographyLevel.PLANNING_AREA].add(unit.year)
            elif dimensions.get("geography_level") == GeographyLevel.SUBZONE.value:
                self._years_by_level[GeographyLevel.SUBZONE].add(unit.year)

    @staticmethod
    def _out_of_domain_attribute(words: str) -> Optional[str]:
        return next((term for term in OUT_OF_DOMAIN_TERMS if _contains(words, term)), None)

    @staticmethod
    def _forecast_requested(words: str) -> bool:
        return any(_contains(words, term) for term in FORECAST_TERMS)

    @staticmethod
    def _underlying_change_requested(words: str) -> bool:
        return any(_contains(words, term) for term in UNDERLYING_CHANGE_TERMS) or re.search(
            r"\b(?:true|actual|underlying)(?:\s+\w+){0,2}\s+change\b", words
        ) is not None

    @staticmethod
    def _independent_prefix(question: str) -> Optional[str]:
        match = re.search(
            r",\s*and\s+(?:why|what\s+explains|explain|had|has|would|say\s+whether|"
            r"forecast|predict|project|how\s+much|what\s+was|what\s+were)",
            question,
            flags=re.IGNORECASE,
        )
        if match:
            prefix = question[: match.start()].strip(" ,")
            return prefix if prefix else None
        return None

    def _expected_result_count(self, query: StructuredQuery) -> int:
        if not query.years:
            return 0
        if query.geography_ambiguous:
            return 0
        if query.all_geographies_at_level and query.geography_level is not None:
            geography_count = len(
                {
                    (item.entity, item.planning_area)
                    for item in self.retriever.registry.geographies
                    if item.level == query.geography_level
                }
            )
        elif query.geographies:
            geography_count = len(query.geographies)
        else:
            geography_count = 1
        non_national = query.geography_level in {
            GeographyLevel.PLANNING_REGION,
            GeographyLevel.PLANNING_AREA,
            GeographyLevel.SUBZONE,
        } or any(item.level != GeographyLevel.NATIONAL for item in query.geographies)
        age_count = len(query.resolved_age_groups) if non_national else 1
        sex_count = len(query.sexes) if non_national else 1
        if non_national and (age_count == 0 or sex_count == 0):
            return 0
        return len(query.years) * geography_count * age_count * sex_count

    def _available_years(self, query: StructuredQuery) -> set[int]:
        if query.geography_level is not None:
            level = query.geography_level
        elif query.geographies and not query.geography_ambiguous:
            level = query.geographies[0].level
        else:
            level = GeographyLevel.NATIONAL
        if level == GeographyLevel.NATIONAL and query.requested_measure:
            return self._years_by_series.get(query.requested_measure, set())
        return self._years_by_level[level]

    def _descriptive_support(
        self,
        query: StructuredQuery,
        results: list[RetrievalResult],
    ) -> tuple[ClaimSupport, list[DecisionReason], list[str]]:
        evidence_ids = [result.evidence.evidence_id for result in results]
        reasons: list[DecisionReason] = []
        if query.measure_status != MeasureStatus.RESOLVED:
            return ClaimSupport.UNSUPPORTED, [DecisionReason.QUERY_NOT_RESOLVED], []
        if query.geography_ambiguous:
            reasons.append(DecisionReason.AMBIGUOUS_GEOGRAPHY)
            if any(
                result.evidence.status == ObservationStatus.NOT_AVAILABLE
                for result in results
            ):
                reasons.append(DecisionReason.VALUE_NOT_AVAILABLE)
            if (
                len({item.level for item in query.geographies}) > 1
                and query.evidence_operation
                in {
                    StructuredOperation.COMPARISON,
                    StructuredOperation.DIFFERENCE,
                    StructuredOperation.ARGMAX,
                }
            ):
                reasons.append(DecisionReason.INCOMPATIBLE_COMPARISON)
            return ClaimSupport.AMBIGUOUS, reasons, evidence_ids
        if "geography_level_name_mismatch" in query.diagnostics:
            return (
                ClaimSupport.UNSUPPORTED,
                [DecisionReason.REQUESTED_GEOGRAPHY_UNAVAILABLE],
                evidence_ids,
            )
        if (
            query.all_geographies_at_level
            and query.evidence_operation != StructuredOperation.ARGMAX
        ):
            return (
                ClaimSupport.UNSUPPORTED,
                [DecisionReason.REQUESTED_GEOGRAPHY_UNAVAILABLE],
                [],
            )
        if not query.years:
            return ClaimSupport.UNSUPPORTED, [DecisionReason.QUERY_NOT_RESOLVED], evidence_ids
        unavailable = [
            result for result in results if result.evidence.status == ObservationStatus.NOT_AVAILABLE
        ]
        if unavailable:
            reasons.append(DecisionReason.VALUE_NOT_AVAILABLE)
        expected = self._expected_result_count(query)
        if expected == 0 or len(results) < expected:
            available_years = self._available_years(query)
            if any(year not in available_years for year in query.years):
                reasons.append(DecisionReason.REQUESTED_YEAR_UNAVAILABLE)
            reasons.append(DecisionReason.MISSING_REQUIRED_OPERANDS)
        if "age_constraint_not_exactly_representable" in query.diagnostics:
            reasons.append(DecisionReason.MISSING_REQUIRED_OPERANDS)
        if reasons:
            support = (
                ClaimSupport.UNAVAILABLE
                if DecisionReason.VALUE_NOT_AVAILABLE in reasons
                else ClaimSupport.UNSUPPORTED
            )
            return support, _unique(reasons), evidence_ids
        return ClaimSupport.SUPPORTED, [DecisionReason.EVIDENCE_COMPLETE], evidence_ids

    @staticmethod
    def _sum_by_year(results: list[RetrievalResult]) -> dict[int, float]:
        values: dict[int, float] = {}
        for result in results:
            if result.evidence.value is not None:
                values[result.evidence.year] = values.get(result.evidence.year, 0.0) + float(
                    result.evidence.value
                )
        return values

    @staticmethod
    def _sum_by_dimension(
        results: list[RetrievalResult], dimension: str
    ) -> dict[str, float]:
        values: dict[str, float] = {}
        for result in results:
            name = result.evidence.dimensions.get(dimension)
            if name is not None and result.evidence.value is not None:
                values[name] = values.get(name, 0.0) + float(result.evidence.value)
        return values

    def _premise_check(
        self,
        words: str,
        query: StructuredQuery,
        results: list[RetrievalResult],
    ) -> Optional[PremiseCheck]:
        if query.operation != StructuredOperation.CAUSAL_EXPLANATION:
            return None
        evidence_ids = [result.evidence.evidence_id for result in results]
        temporal = self._sum_by_year(results)
        if len(query.years) >= 2 and all(year in temporal for year in query.years[:2]):
            first, second = query.years[:2]
            first_value, second_value = temporal[first], temporal[second]
            decrease = any(
                _contains(words, term)
                for term in ("fall", "fell", "decline", "decrease", "lower")
            )
            increase = any(
                _contains(words, term)
                for term in ("rise", "rose", "increase", "grew", "higher")
            )
            if decrease or increase:
                actual = "increase" if second_value > first_value else "decrease" if second_value < first_value else "equal"
                expected = "decrease" if decrease else "increase"
                return PremiseCheck(
                    relation=expected,
                    actual_relation=actual,
                    contradicted=actual != expected,
                    evidence_ids=evidence_ids,
                )
        if any(_contains(words, term) for term in ("more", "outnumber")):
            if len(query.geographies) >= 2:
                dimension = (
                    "planning_region"
                    if query.geographies[0].level == GeographyLevel.PLANNING_REGION
                    else "planning_area"
                    if query.geographies[0].level == GeographyLevel.PLANNING_AREA
                    else "subzone"
                )
                values = self._sum_by_dimension(results, dimension)
                first, second = query.geographies[0].entity, query.geographies[1].entity
            elif len(query.sexes) >= 2:
                values = self._sum_by_dimension(results, "sex")
                first, second = query.sexes[:2]
            else:
                return None
            if first in values and second in values:
                actual = "greater" if values[first] > values[second] else "not_greater"
                return PremiseCheck(
                    relation=f"{first}_greater_than_{second}",
                    actual_relation=actual,
                    contradicted=actual != "greater",
                    evidence_ids=evidence_ids,
                )
        return None

    @staticmethod
    def _classify(claims: list[AnswerabilityClaim]) -> AnswerabilityClassification:
        supported = sum(claim.support == ClaimSupport.SUPPORTED for claim in claims)
        if supported == len(claims):
            return AnswerabilityClassification.ANSWERABLE
        if supported:
            return AnswerabilityClassification.PARTIALLY_ANSWERABLE
        if all(claim.support == ClaimSupport.OUT_OF_SCOPE for claim in claims):
            return AnswerabilityClassification.OUT_OF_SCOPE
        return AnswerabilityClassification.INSUFFICIENT_EVIDENCE

    def decide(self, question: str) -> DeterministicAnswerabilityDecision:
        words = _normalise(question)
        plan = self.retriever.plan(question)
        query = plan.query
        results = plan.results
        claims: list[AnswerabilityClaim] = []
        reasons: list[DecisionReason] = []
        out_of_domain = self._out_of_domain_attribute(words)
        forecast = self._forecast_requested(words)
        causal = query.operation == StructuredOperation.CAUSAL_EXPLANATION
        underlying = self._underlying_change_requested(words)
        prefix = self._independent_prefix(question)

        support, support_reasons, evidence_ids = self._descriptive_support(query, results)
        main_descriptive_independent = not causal and not forecast and out_of_domain is None
        if causal and prefix is not None:
            main_descriptive_independent = True
        if underlying:
            main_descriptive_independent = True

        if main_descriptive_independent and support == ClaimSupport.SUPPORTED:
            claims.append(
                AnswerabilityClaim(
                    kind=ClaimKind.DESCRIPTIVE,
                    description=f"descriptive operation: {query.evidence_operation.value}",
                    support=support,
                    evidence_ids=evidence_ids,
                    reasons=support_reasons,
                )
            )
            reasons.extend(support_reasons)
        elif support != ClaimSupport.SUPPORTED and out_of_domain is None and not forecast:
            prefix_supported = False
            if prefix is not None:
                prefix_plan = self.retriever.plan(prefix)
                prefix_support, prefix_reasons, prefix_ids = self._descriptive_support(
                    prefix_plan.query, prefix_plan.results
                )
                if prefix_support == ClaimSupport.SUPPORTED:
                    claims.append(
                        AnswerabilityClaim(
                            kind=ClaimKind.DESCRIPTIVE,
                            description="independently requested descriptive clause",
                            support=ClaimSupport.SUPPORTED,
                            evidence_ids=prefix_ids,
                            reasons=prefix_reasons,
                        )
                    )
                    reasons.extend(prefix_reasons)
                    prefix_supported = True
            if not (causal and prefix_supported):
                claims.append(
                    AnswerabilityClaim(
                        kind=ClaimKind.DESCRIPTIVE,
                        description=f"descriptive operation: {query.evidence_operation.value}",
                        support=support,
                        evidence_ids=evidence_ids,
                        reasons=support_reasons,
                    )
                )
                reasons.extend(support_reasons)

        if causal:
            claims.append(
                AnswerabilityClaim(
                    kind=ClaimKind.CAUSAL,
                    description="causal explanation",
                    support=ClaimSupport.UNSUPPORTED,
                    reasons=[DecisionReason.CAUSAL_EVIDENCE_MISSING],
                )
            )
            reasons.append(DecisionReason.CAUSAL_EVIDENCE_MISSING)
        if forecast:
            if prefix is not None and not any(
                claim.support == ClaimSupport.SUPPORTED for claim in claims
            ):
                prefix_plan = self.retriever.plan(prefix)
                prefix_support, prefix_reasons, prefix_ids = self._descriptive_support(
                    prefix_plan.query, prefix_plan.results
                )
                if prefix_support == ClaimSupport.SUPPORTED:
                    claims.append(
                        AnswerabilityClaim(
                            kind=ClaimKind.DESCRIPTIVE,
                            description="independently requested historical clause",
                            support=ClaimSupport.SUPPORTED,
                            evidence_ids=prefix_ids,
                            reasons=prefix_reasons,
                        )
                    )
                    reasons.extend(prefix_reasons)
            claims.append(
                AnswerabilityClaim(
                    kind=ClaimKind.FORECAST,
                    description="future prediction or projection",
                    support=ClaimSupport.UNSUPPORTED,
                    reasons=[DecisionReason.PREDICTION_REQUESTED],
                )
            )
            reasons.append(DecisionReason.PREDICTION_REQUESTED)
        if out_of_domain is not None:
            if prefix is not None:
                prefix_plan = self.retriever.plan(prefix)
                prefix_support, prefix_reasons, prefix_ids = self._descriptive_support(
                    prefix_plan.query, prefix_plan.results
                )
                if prefix_support == ClaimSupport.SUPPORTED:
                    claims.append(
                        AnswerabilityClaim(
                            kind=ClaimKind.DESCRIPTIVE,
                            description="independently requested population clause",
                            support=ClaimSupport.SUPPORTED,
                            evidence_ids=prefix_ids,
                            reasons=prefix_reasons,
                        )
                    )
                    reasons.extend(prefix_reasons)
            claims.append(
                AnswerabilityClaim(
                    kind=ClaimKind.OUT_OF_DOMAIN,
                    description=f"attribute outside corpus: {out_of_domain}",
                    support=ClaimSupport.OUT_OF_SCOPE,
                    reasons=[DecisionReason.UNSUPPORTED_ATTRIBUTE],
                )
            )
            reasons.append(DecisionReason.UNSUPPORTED_ATTRIBUTE)
        if underlying:
            crossing_boundary = (
                query.geography_level == GeographyLevel.PLANNING_REGION
                and 2019 in query.years
                and any(year >= 2020 for year in query.years)
            )
            if crossing_boundary:
                claims.append(
                    AnswerabilityClaim(
                        kind=ClaimKind.UNDERLYING_CHANGE,
                        description="unqualified underlying change across geography definitions",
                        support=ClaimSupport.UNSUPPORTED,
                        reasons=[DecisionReason.BOUNDARY_DEFINITION_CHANGE],
                    )
                )
                reasons.append(DecisionReason.BOUNDARY_DEFINITION_CHANGE)
        elif (
            query.geography_level == GeographyLevel.PLANNING_REGION
            and 2019 in query.years
            and any(year >= 2020 for year in query.years)
        ):
            reasons.append(DecisionReason.BOUNDARY_DEFINITION_CHANGE)

        premise_check = self._premise_check(words, query, results)
        if premise_check is not None and premise_check.contradicted:
            reasons.append(DecisionReason.FALSE_PREMISE)

        if not claims:
            claims.append(
                AnswerabilityClaim(
                    kind=ClaimKind.DESCRIPTIVE,
                    description="unresolved requested information",
                    support=ClaimSupport.UNSUPPORTED,
                    reasons=[DecisionReason.QUERY_NOT_RESOLVED],
                )
            )
            reasons.append(DecisionReason.QUERY_NOT_RESOLVED)

        classification = self._classify(claims)
        if classification == AnswerabilityClassification.PARTIALLY_ANSWERABLE:
            reasons.append(DecisionReason.PARTIAL_SUPPORT)
        all_retrieved = list(
            dict.fromkeys(
                [result.evidence.evidence_id for result in results]
                + [evidence_id for claim in claims for evidence_id in claim.evidence_ids]
            )
        )
        return DeterministicAnswerabilityDecision(
            classification=classification,
            claims=claims,
            reasons=_unique(reasons),
            retrieved_evidence_ids=all_retrieved,
            structured_query=query,
            premise_check=premise_check,
        )
