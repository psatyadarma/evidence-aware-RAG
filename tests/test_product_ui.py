from pathlib import Path

from streamlit.testing.v1 import AppTest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_streamlit_page_starts_and_exposes_required_controls():
    app = AppTest.from_file(str(PROJECT_ROOT / "streamlit_app.py"), default_timeout=30).run()

    assert not app.exception
    assert app.title[0].value == "Evidence-Aware Singapore Population Assistant"
    assert len(app.text_area) == 1
    assert app.text_area[0].label == "Question"
    assert any(button.label == "Ask" for button in app.button)
    assert any(item.label == "About and limitations" for item in app.expander)
