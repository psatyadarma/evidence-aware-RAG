"""Stable evidence identifiers shared by corpus and evaluation code."""

from __future__ import annotations

import re

from app.models import PopulationObservation, ProcessedDemographicRecord


class EvidenceIdError(ValueError):
    """An observation cannot be represented by a stable evidence ID."""


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise EvidenceIdError(f"cannot create evidence ID from {value!r}")
    return slug


def observation_evidence_id(
    record: ProcessedDemographicRecord, observation: PopulationObservation
) -> str:
    base = f"{record.source_id}:r{record.record_id}:y{observation.year}"
    suffix = "".join(
        f":{key}={_slug(value)}"
        for key, value in sorted(observation.dimensions.items())
    )
    return base + suffix

