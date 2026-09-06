"""Framework-neutral presentation helpers used by the Streamlit page."""

from __future__ import annotations

from typing import Any

from app.controller import EvidenceView, ResultView


def result_tone(result: ResultView) -> str:
    if result.error_code is not None:
        return "error" if result.status == "ERROR" else "warning"
    if result.status == "ANSWERABLE":
        return "success"
    if result.status == "PARTIALLY_ANSWERABLE":
        return "warning"
    return "info"


def evidence_rows(evidence: list[EvidenceView]) -> list[dict[str, Any]]:
    return [
        {
            "Dataset": item.dataset,
            "Geography": item.geography,
            "Year": item.year,
            "Measure": item.measure,
            "Demographics": ", ".join(
                f"{key}: {value}" for key, value in item.demographics.items()
            ) or "All residents",
            "Value": item.value if item.value is not None else item.raw_value or "Not available",
            "Status": item.status,
            "Evidence ID": item.evidence_id,
            "Publisher": item.publisher,
            "Source URL": item.source_url,
        }
        for item in evidence
    ]


def transparency_rows(result: ResultView) -> list[tuple[str, str]]:
    labels = {
        "interpreted_measure": "Interpreted measure",
        "years": "Year(s)",
        "geography_level": "Geography level",
        "geographies": "Geography",
        "age_filter": "Age filter",
        "resolved_age_groups": "Resolved age groups",
        "sexes": "Sex",
        "requested_operation": "Requested operation",
        "evidence_operation": "Evidence operation",
        "answerability_decision": "Answerability decision",
        "generation_invoked": "LLM generation invoked",
    }
    rows = []
    for key, label in labels.items():
        value = result.transparency.get(key)
        if value not in (None, [], {}):
            rows.append((label, str(value)))
    return rows
