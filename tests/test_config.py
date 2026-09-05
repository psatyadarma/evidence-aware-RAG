import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_have_safe_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.retrieval_top_k == 5
    assert settings.retrieval_threshold is None
    assert settings.model_api_key is None
    assert settings.model_name == "gpt-4.1-mini-2025-04-14"
    assert settings.model_timeout_seconds == 60.0
    assert settings.model_max_attempts == 2
    assert settings.model_max_output_tokens == 1000


def test_settings_read_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_RETRIEVAL_TOP_K", "7")
    monkeypatch.setenv("RAG_RETRIEVAL_THRESHOLD", "0.72")
    monkeypatch.setenv("RAG_MODEL_API_KEY", "not-a-real-key")
    monkeypatch.setenv("RAG_MODEL_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("RAG_MODEL_MAX_ATTEMPTS", "3")

    settings = Settings(_env_file=None)

    assert settings.retrieval_top_k == 7
    assert settings.retrieval_threshold == 0.72
    assert settings.model_api_key is not None
    assert settings.model_api_key.get_secret_value() == "not-a-real-key"
    assert settings.model_timeout_seconds == 30.0
    assert settings.model_max_attempts == 3


@pytest.mark.parametrize("threshold", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_threshold_is_rejected(threshold: float) -> None:
    with pytest.raises(ValidationError):
        Settings(retrieval_threshold=threshold, _env_file=None)


def test_api_key_is_masked_in_serialized_settings() -> None:
    settings = Settings(model_api_key="secret-value", _env_file=None)

    assert str(settings.model_dump()["model_api_key"]) == "**********"
