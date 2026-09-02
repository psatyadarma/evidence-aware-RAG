"""Environment-backed application configuration."""

import math
from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from RAG_* environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="RAG_",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    retrieval_top_k: int = Field(default=5, ge=1)
    retrieval_threshold: Optional[float] = None
    model_provider: str = Field(default="openai", min_length=1)
    model_name: str = Field(default="gpt-4.1-mini", min_length=1)
    model_api_key: Optional[SecretStr] = None

    @field_validator("retrieval_threshold")
    @classmethod
    def threshold_must_be_finite(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not math.isfinite(value):
            raise ValueError("retrieval threshold must be finite")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""

    return Settings()
