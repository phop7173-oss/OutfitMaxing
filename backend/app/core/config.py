"""Environment-based configuration.

Business policies are intentionally NOT given default values: they are PENDING final business
approval (see docs/POLICIES.md). Unset policies are None and must be handled explicitly by the
code that needs them. Proposed values live only as comments in .env.example.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives in the repository root; also accept backend/.env. Missing files are ignored.
_BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILES = (_BACKEND_DIR.parent / ".env", _BACKEND_DIR / ".env")

DEV_DB_URL = "postgresql+psycopg://outfitmaxing:outfitmaxing@localhost:5432/outfitmaxing"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,  # "KEY=" means not set, not an empty string
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = DEV_DB_URL
    redis_url: str = "redis://localhost:6379/0"

    # Technical default from the master prompt (not a business policy that needs approval).
    reservation_ttl_minutes: int = Field(default=15, ge=1)

    # Interim guard for catalog write endpoints until Phase 6 login/roles.
    # Unset means the admin API is disabled.
    admin_api_key: SecretStr | None = None

    # AI provider abstraction (provider implementation arrives in Phase 4).
    ai_provider: str = "gemini"
    ai_model: str | None = None
    ai_api_key: SecretStr | None = None
    ai_daily_request_limit: int | None = Field(default=None, ge=1)
    ai_per_user_hourly_limit: int | None = Field(default=None, ge=1)

    # Telegram (used from Phase 4).
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_channel_id: str | None = None
    telegram_webhook_url: str | None = None

    # Business policies: PENDING final business approval. None = not configured.
    policy_cancellation_rule: str | None = None
    policy_return_window_days: int | None = Field(default=None, ge=0)
    policy_refund_after_inspection: bool | None = None
    policy_preorder_deposit_percent: int | None = Field(default=None, ge=0, le=100)
    policy_cod_max_order_mmk: int | None = Field(default=None, ge=0)
    policy_cod_manual_review_above_mmk: int | None = Field(default=None, ge=0)
    policies_approved_for_launch: bool = False

    @field_validator("admin_api_key")
    @classmethod
    def _admin_key_strength(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and len(v.get_secret_value()) < 16:
            raise ValueError("ADMIN_API_KEY must be at least 16 characters")
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.app_env == "production" and (
            "outfitmaxing:outfitmaxing@" in self.database_url or "change-me" in self.database_url
        ):
            raise ValueError(
                "Production requires a non-default DATABASE_URL (no example credentials)"
            )
        return self

    def secret_values(self) -> list[str]:
        """Exact secret values to redact from logs (best-effort, see app/core/logging.py)."""
        values: list[str] = []
        for secret in (
            self.admin_api_key,
            self.ai_api_key,
            self.telegram_bot_token,
            self.telegram_webhook_secret,
        ):
            if secret is not None:
                values.append(secret.get_secret_value())
        for url in (self.database_url, self.redis_url):
            try:
                password = urlsplit(url).password
            except ValueError:
                password = None
            if password:
                values.append(password)
        return values


@lru_cache
def get_settings() -> Settings:
    return Settings()
