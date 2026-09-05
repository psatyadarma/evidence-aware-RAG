"""Deterministic computation over evidence approved by the frozen gate."""

from __future__ import annotations

import hashlib
import json
import math
from enum import Enum
from typing import Iterable, Optional, Union

from pydantic import Field, model_validator

from app.answerability import ClaimSupport, DeterministicAnswerabilityDecision
from app.models import ObservationStatus, RetrievalUnit, StrictModel
from app.structured_retrieval import GeographyLevel, StructuredOperation


Number = Union[int, float]


class ComputationError(ValueError):
    """Supported evidence cannot be transformed under the declared operation."""


class VerifiedFactKind(str, Enum):
    LOOKUP = "lookup"
    DIFFERENCE = "difference"
    COMPARISON = "comparison"
    SUM = "sum"
    ARGMAX = "argmax"
    DESCRIPTIVE_TREND = "descriptive_trend"


class FactOperand(StrictModel):
    evidence_id: str = Field(min_length=1)
    year: int
    value: Number
    label: str = Field(min_length=1)
    dimensions: dict[str, str]


class SourceQualification(StrictModel):
    qualification_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class VerifiedFact(StrictModel):
    fact_id: str = Field(pattern=r"^fact-[0-9a-f]{16}$")
    kind: VerifiedFactKind
    statement: str = Field(min_length=1)
    numeric_value: Optional[Number]
    categorical_value: Optional[str]
    result_evidence_id: Optional[str]
    operands: list[FactOperand] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def result_and_evidence_are_consistent(self) -> "VerifiedFact":
        if self.numeric_value is None and self.categorical_value is None:
            raise ValueError("a verified fact requires a numeric or categorical result")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("verified-fact evidence IDs must be unique")
        operand_ids = [item.evidence_id for item in self.operands]
        if operand_ids != self.evidence_ids:
            raise ValueError("operand order must exactly match evidence_ids")
        if self.result_evidence_id is not None and self.result_evidence_id not in self.evidence_ids:
            raise ValueError("result_evidence_id must be one of the fact's evidence IDs")
        return self


class VerifiedComputation(StrictModel):
    operation: VerifiedFactKind
    facts: list[VerifiedFact] = Field(min_length=1, max_length=1)
    qualifications: list[SourceQualification] = Field(default_factory=list)


def _as_number(value: Number) -> Number:
    result = float(value)
    if not math.isfinite(result):
        raise ComputationError("evidence contains a non-finite numeric value")
    return int(result) if result.is_integer() else result


def _format_number(value: Number) -> str:
    value = _as_number(value)
    return f"{value:,}" if isinstance(value, int) else f"{value:,.6f}".rstrip("0").rstrip(".")


def _label(unit: RetrievalUnit) -> str:
    dimensions = unit.dimensions
    entity = (
        dimensions.get("planning_region")
        or dimensions.get("subzone")
        or dimensions.get("planning_area")
        or dimensions.get("geography")
        or "Singapore"
    )
    parts = [entity, str(unit.year)]
    if dimensions.get("series"):
        parts.append(dimensions["series"])
    if dimensions.get("age_group") and dimensions["age_group"] != "Total":
        parts.append(f"age {dimensions['age_group']}")
    if dimensions.get("sex") and dimensions["sex"] != "Total":
        parts.append(dimensions["sex"])
    return ", ".join(parts)


def _fact_id(operation: VerifiedFactKind, evidence_ids: list[str]) -> str:
    material = json.dumps(
        {"operation": operation.value, "evidence_ids": evidence_ids},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "fact-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _qualification_set(units: list[RetrievalUnit]) -> list[SourceQualification]:
    qualifications: list[SourceQualification] = []
    region_ids = [
        unit.evidence_id
        for unit in units
        if unit.source_id == "residents_by_planning_region_annual"
    ]
    if region_ids:
        qualifications.append(
            SourceQualification(
                qualification_id="planning-region-rounded-nearest-10",
                text="Planning-region values are rounded to the nearest 10 and may not add exactly.",
                evidence_ids=region_ids,
            )
        )
        years = {unit.year for unit in units if unit.evidence_id in region_ids}
        if 2019 in years and any(year >= 2020 for year in years):
            qualifications.append(
                SourceQualification(
                    qualification_id="planning-region-boundary-change-2019-2020",
                    text=(
                        "The 2019 planning-region value uses URA Master Plan 2014 boundaries, "
                        "while 2020 onward uses Master Plan 2019 boundaries; the published "
                        "difference is not an unqualified underlying population change."
                    ),
                    evidence_ids=region_ids,
                )
            )
    census_ids = [
        unit.evidence_id
        for unit in units
        if unit.source_id == "resident_population_census_2020"
    ]
    if census_ids:
        qualifications.append(
            SourceQualification(
                qualification_id="census-2020-snapshot-mp2019",
                text=(
                    "This is a Census 2020 snapshot using URA Master Plan 2019 planning "
                    "geographies; it does not establish change outside 2020."
                ),
                evidence_ids=census_ids,
            )
        )
    return qualifications


class DeterministicComputationEngine:
    """Compute only from observations attached to gate-supported claims."""

    def __init__(self, corpus_units: Iterable[RetrievalUnit]) -> None:
        units = tuple(corpus_units)
        self._by_id = {unit.evidence_id: unit for unit in units}
        if len(self._by_id) != len(units):
            raise ComputationError("corpus contains duplicate evidence IDs")

    def _supported_units(
        self, decision: DeterministicAnswerabilityDecision
    ) -> list[RetrievalUnit]:
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for claim in decision.claims
                if claim.support == ClaimSupport.SUPPORTED
                for evidence_id in claim.evidence_ids
            )
        )
        if not evidence_ids:
            raise ComputationError("decision has no gate-supported evidence")
        missing = [item for item in evidence_ids if item not in self._by_id]
        if missing:
            raise ComputationError("supported evidence is absent from the corpus: " + ", ".join(missing))
        units = [self._by_id[item] for item in evidence_ids]
        invalid = [
            unit.evidence_id
            for unit in units
            if unit.status != ObservationStatus.OBSERVED or unit.value is None
        ]
        if invalid:
            raise ComputationError(
                "supported computation cannot use NOT_AVAILABLE observations: "
                + ", ".join(invalid)
            )
        return units

    @staticmethod
    def _operation(
        decision: DeterministicAnswerabilityDecision,
        units: list[RetrievalUnit],
    ) -> VerifiedFactKind:
        if len(units) == 1:
            return VerifiedFactKind.LOOKUP
        mapping = {
            StructuredOperation.LOOKUP: VerifiedFactKind.LOOKUP,
            StructuredOperation.DIFFERENCE: VerifiedFactKind.DIFFERENCE,
            StructuredOperation.COMPARISON: VerifiedFactKind.COMPARISON,
            StructuredOperation.SUM: VerifiedFactKind.SUM,
            StructuredOperation.ARGMAX: VerifiedFactKind.ARGMAX,
            StructuredOperation.DESCRIPTIVE_TREND: VerifiedFactKind.DESCRIPTIVE_TREND,
        }
        try:
            return mapping[decision.structured_query.evidence_operation]
        except KeyError as exc:
            raise ComputationError(
                "the supported claim has no deterministic computation operation"
            ) from exc

    @staticmethod
    def _ordered_units(
        operation: VerifiedFactKind,
        decision: DeterministicAnswerabilityDecision,
        units: list[RetrievalUnit],
    ) -> list[RetrievalUnit]:
        if operation in {VerifiedFactKind.DIFFERENCE, VerifiedFactKind.COMPARISON, VerifiedFactKind.DESCRIPTIVE_TREND}:
            if len({unit.year for unit in units}) > 1:
                return sorted(units, key=lambda unit: (unit.year, unit.evidence_id))
            geography_order = {
                item.entity: index
                for index, item in enumerate(decision.structured_query.geographies)
            }
            sex_order = {
                item: index for index, item in enumerate(decision.structured_query.sexes)
            }
            return sorted(
                units,
                key=lambda unit: (
                    geography_order.get(
                        unit.dimensions.get("planning_region")
                        or unit.dimensions.get("planning_area")
                        or unit.dimensions.get("subzone", ""),
                        len(geography_order),
                    ),
                    sex_order.get(unit.dimensions.get("sex", ""), len(sex_order)),
                    unit.evidence_id,
                ),
            )
        return list(units)

    def compute(
        self, decision: DeterministicAnswerabilityDecision
    ) -> VerifiedComputation:
        units = self._supported_units(decision)
        operation = self._operation(decision, units)
        units = self._ordered_units(operation, decision, units)
        operands = [
            FactOperand(
                evidence_id=unit.evidence_id,
                year=unit.year,
                value=_as_number(unit.value),  # type: ignore[arg-type]
                label=_label(unit),
                dimensions=dict(sorted(unit.dimensions.items())),
            )
            for unit in units
        ]
        evidence_ids = [item.evidence_id for item in operands]
        numeric: Optional[Number] = None
        categorical: Optional[str] = None
        result_evidence_id: Optional[str] = None

        if operation == VerifiedFactKind.LOOKUP:
            if len(operands) != 1:
                raise ComputationError("lookup requires exactly one supported observation")
            numeric = operands[0].value
            statement = f"{operands[0].label}: {_format_number(numeric)} residents."
        elif operation == VerifiedFactKind.SUM:
            numeric = _as_number(sum(float(item.value) for item in operands))
            statement = (
                f"The sum of {len(operands)} supplied observations is "
                f"{_format_number(numeric)} residents."
            )
        elif operation in {
            VerifiedFactKind.DIFFERENCE,
            VerifiedFactKind.DESCRIPTIVE_TREND,
        }:
            if len(operands) != 2:
                raise ComputationError(f"{operation.value} requires exactly two supported observations")
            temporal = operands[0].year != operands[1].year
            numeric = _as_number(
                float(operands[1].value) - float(operands[0].value)
                if temporal
                else float(operands[0].value) - float(operands[1].value)
            )
            direction = "increased" if numeric > 0 else "decreased" if numeric < 0 else "did not change"
            magnitude = _format_number(abs(numeric))
            if temporal:
                statement = (
                    f"From {operands[0].label} ({_format_number(operands[0].value)}) to "
                    f"{operands[1].label} ({_format_number(operands[1].value)}), the published "
                    f"count {direction} by {magnitude} residents."
                )
            else:
                relation = "more" if numeric > 0 else "fewer" if numeric < 0 else "the same number of"
                statement = (
                    f"{operands[0].label} ({_format_number(operands[0].value)}) had "
                    f"{magnitude} {relation} residents than {operands[1].label} "
                    f"({_format_number(operands[1].value)})."
                )
        elif operation == VerifiedFactKind.COMPARISON:
            if len(operands) != 2:
                raise ComputationError("comparison requires exactly two supported observations")
            first, second = operands
            if first.value > second.value:
                categorical, winner = "first", first
            elif second.value > first.value:
                categorical, winner = "second", second
            else:
                categorical, winner = "equal", None
            numeric = _as_number(float(second.value) - float(first.value))
            result_evidence_id = winner.evidence_id if winner is not None else None
            if winner is None:
                statement = f"{first.label} and {second.label} are equal at {_format_number(first.value)} residents."
            else:
                statement = (
                    f"{winner.label} is higher; the supplied values are "
                    f"{_format_number(first.value)} and {_format_number(second.value)} residents, "
                    f"a gap of {_format_number(abs(numeric))}."
                )
        elif operation == VerifiedFactKind.ARGMAX:
            maximum = max(item.value for item in operands)
            winners = [item for item in operands if item.value == maximum]
            if len(winners) != 1:
                raise ComputationError("argmax is tied and has no unique result")
            winner = winners[0]
            categorical = winner.label
            numeric = winner.value
            result_evidence_id = winner.evidence_id
            statement = (
                f"{winner.label} has the largest supplied value: "
                f"{_format_number(winner.value)} residents."
            )
        else:  # pragma: no cover - exhaustive enum guard
            raise ComputationError(f"unsupported operation {operation.value}")

        fact = VerifiedFact(
            fact_id=_fact_id(operation, evidence_ids),
            kind=operation,
            statement=statement,
            numeric_value=numeric,
            categorical_value=categorical,
            result_evidence_id=result_evidence_id,
            operands=operands,
            evidence_ids=evidence_ids,
        )
        return VerifiedComputation(
            operation=operation,
            facts=[fact],
            qualifications=_qualification_set(units),
        )
