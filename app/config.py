"""
Configuration module — the ONLY place environment variables are read.

All settings are read once at startup through a typed, frozen Pydantic
BaseSettings object.  Nothing else in the codebase imports os.environ or
reads dotfiles directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Placeholder sentinel — treat as "no key"
# ---------------------------------------------------------------------------
_NO_KEY_SENTINELS: frozenset[str] = frozenset({"YOUR_GEMINI_API_KEY_HERE", "", "none", "null"})

# ---------------------------------------------------------------------------
# Default constants (never hardcode the model elsewhere)
# ---------------------------------------------------------------------------
DEFAULT_GEMINI_MODEL: str = "gemini-3.5-flash-lite"


class GeminiSettings(BaseSettings):
    """Gemini API sub-settings."""

    model_config = SettingsConfigDict(env_prefix="GEMINI_", extra="ignore")

    api_key: SecretStr = Field(
        default=SecretStr("YOUR_GEMINI_API_KEY_HERE"),
        description="Gemini API key — server-side only, never logged or returned.",
    )
    model: str = Field(default=DEFAULT_GEMINI_MODEL, description="Gemini model identifier.")
    timeout_s: float = Field(default=30.0, ge=1.0, le=120.0)
    max_output_tokens: int = Field(default=2048, ge=256, le=8192)
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)

    @property
    def api_key_configured(self) -> bool:
        """Return True only when a real (non-placeholder) key is set."""
        raw = self.api_key.get_secret_value().strip()
        return raw not in _NO_KEY_SENTINELS


class Settings(BaseSettings):
    """Top-level application settings — frozen after startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # Gemini sub-settings (populated from GEMINI_* env vars)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)

    # Rate limiting
    rate_limit_per_min: int = Field(default=20, ge=1, le=1000, alias="RATE_LIMIT_PER_MIN")
    rate_limit_ideas_per_min: int = Field(default=5, ge=1, le=100, alias="RATE_LIMIT_IDEAS_PER_MIN")

    # Cache
    cache_ttl_s: int = Field(default=3600, ge=0, alias="CACHE_TTL_S")

    # CORS — comma-separated origins, empty means same-origin only
    allowed_origins: str = Field(default="", alias="ALLOWED_ORIGINS")

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", alias="LOG_LEVEL"
    )

    # Request body size limit (bytes)
    max_body_size_bytes: int = Field(default=65536, ge=1024, alias="MAX_BODY_SIZE_BYTES")

    # Environment
    env: Literal["development", "production", "test"] = Field(default="development", alias="ENV")

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def strip_origins(cls, v: object) -> str:
        """Normalise allowed origins string."""
        return str(v).strip() if v else ""

    @model_validator(mode="after")
    def check_production_settings(self) -> Settings:
        """Warn (don't crash) when running production without a real key."""
        return self

    @property
    def cors_origins(self) -> list[str]:
        """Return parsed list of allowed CORS origins."""
        if not self.allowed_origins:
            return []
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def openapi_enabled(self) -> bool:
        """Expose OpenAPI docs only in non-production environments."""
        return self.env != "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached after first call)."""
    return Settings()
