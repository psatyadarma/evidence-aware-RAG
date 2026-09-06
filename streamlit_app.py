"""Streamlit presentation layer for the frozen evidence-aware pipeline."""

from __future__ import annotations

import logging

import streamlit as st

from app.controller import ApplicationController
from app.config import Settings
from app.runtime import RuntimeValidationError, build_runtime
from app.ui import evidence_rows, result_tone, transparency_rows


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

SAMPLE_QUESTIONS = {
    "Direct lookup": "What was Singapore's resident population in 2024?",
    "Comparison": "Was Singapore's total population higher in 2023 or 2024?",
    "Aggregation": "What was the combined resident population of the East and West regions in 2024?",
    "Unsupported cause": "Why did Singapore's total population change in 2024?",
    "Outside the corpus": "What was Singapore's median household income in 2024?",
}


@st.cache_resource(show_spinner="Validating the local corpus…")
def _controller() -> ApplicationController:
    settings = Settings()
    logging.getLogger().setLevel(getattr(logging, settings.log_level))
    return ApplicationController(build_runtime(settings=settings))


def _set_sample(label: str) -> None:
    st.session_state["question"] = SAMPLE_QUESTIONS[label]


def _render_result(result) -> None:
    tone = result_tone(result)
    getattr(st, tone)(f"Status: {result.status}")
    st.write(result.answer or result.message)
    if result.limitations:
        st.markdown("**Limitations**")
        for limitation in result.limitations:
            st.warning(limitation)
    if result.refusal_reasons:
        st.markdown("**Why the system refused**")
        for reason in result.refusal_reasons:
            st.write(f"- {reason}")
    if result.qualifications:
        st.markdown("**Source qualifications**")
        for qualification in result.qualifications:
            st.info(qualification)
    if result.evidence:
        st.markdown("### Evidence and provenance")
        for item in evidence_rows(result.evidence):
            with st.container(border=True):
                st.markdown(f"**{item['Dataset']}** — {item['Publisher']}")
                st.write(
                    f"{item['Geography']} · {item['Year']} · {item['Measure']} · "
                    f"{item['Demographics']}"
                )
                st.write(f"Value: **{item['Value']}** ({item['Status']})")
                st.markdown(f"[Official source]({item['Source URL']})")
                st.caption(f"Evidence ID: {item['Evidence ID']}")
    with st.expander("How this answer was produced"):
        for label, value in transparency_rows(result):
            st.write(f"**{label}:** {value}")


def main() -> None:
    st.set_page_config(
        page_title="Evidence-Aware Singapore Population Assistant",
        page_icon="📊",
        layout="centered",
    )
    st.title("Evidence-Aware Singapore Population Assistant")
    st.caption(
        "Answers questions using selected official Singapore population datasets and "
        "abstains when the available evidence is insufficient."
    )
    try:
        controller = _controller()
    except RuntimeValidationError as exc:
        st.error(f"Startup validation failed: {exc}")
        st.stop()
        return
    if not controller.runtime.api_key_configured:
        st.info(
            "RAG_MODEL_API_KEY is not configured. Deterministic refusals work normally; "
            "supported questions will show setup guidance instead of calling a model."
        )

    st.markdown("#### Try an example")
    columns = st.columns(len(SAMPLE_QUESTIONS))
    for column, label in zip(columns, SAMPLE_QUESTIONS):
        column.button(label, on_click=_set_sample, args=(label,), use_container_width=True)

    with st.form("question_form"):
        question = st.text_area(
            "Question",
            key="question",
            placeholder="Ask about a supported population measure, year, or geography…",
            height=100,
        )
        submitted = st.form_submit_button("Ask", type="primary")
    if submitted:
        with st.spinner("Checking the available evidence…"):
            _render_result(controller.answer(question))

    with st.expander("About and limitations"):
        st.markdown(
            """
- The corpus contains only three selected official Singapore population/demographic datasets.
- Planning areas and subzones are Census-2020-only; planning-region coverage starts in 2019.
- Descriptive observations do not establish causes, and unsupported values are not forecast.
- Deterministic interpretation can fail on unfamiliar wording, measures, and ranges.
- Conservative abstention is intentional. This prototype is not an authoritative statistical service.
- Submitted questions may be sent to the configured model provider only after deterministic evidence checks allow generation.
"""
        )


if __name__ == "__main__":
    main()
