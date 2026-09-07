from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, StringConstraints, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEVFEED_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    database_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    redis_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    cors_origins: list[str] = []
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    job_log_max_entries: int = Field(default=1000, ge=100, le=10000)
    job_log_ttl_seconds: int = Field(default=604800, ge=3600, le=2592000)
    feed_user_agent: str = "DevFeed/0.1 (+https://devfeed.tech)"
    feed_max_bytes: int = Field(default=5_000_000, ge=1024, le=20_000_000)
    feed_max_entries: int = Field(default=500, ge=1, le=2000)
    feed_timeout_seconds: int = Field(default=20, ge=1, le=30)
    page_max_bytes: int = Field(default=2_000_000, ge=1024, le=5_000_000)
    page_timeout_seconds: int = Field(default=15, ge=1, le=30)
    scheduler_batch_size: int = Field(default=100, ge=1, le=1000)
    cache_enabled: bool = True
    cache_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    cache_metadata_ttl_seconds: int = Field(default=600, ge=1, le=3600)
    cache_max_bytes: int = Field(default=1_000_000, ge=1024, le=5_000_000)
    ai_enabled: bool = False
    codex_app_server_url: str | None = None
    codex_model: str | None = None
    codex_auth_token: SecretStr | None = None
    codex_timeout_seconds: int = Field(default=90, ge=10, le=120)
    notifications_enabled: bool = False

    @model_validator(mode="after")
    def validate_ai_configuration(self):
        from urllib.parse import urlsplit

        if self.ai_enabled and (
            not self.codex_app_server_url or not self.codex_model or not self.codex_model.strip()
        ):
            raise ValueError("AI requires DEVFEED_CODEX_APP_SERVER_URL and DEVFEED_CODEX_MODEL")
        if self.codex_app_server_url:
            url = urlsplit(self.codex_app_server_url)
            if url.scheme == "unix":
                if (
                    url.netloc
                    or not url.path.startswith("/")
                    or url.path == "/"
                    or url.query
                    or url.fragment
                    or "%" in url.path
                    or "\x00" in url.path
                ):
                    raise ValueError("Codex Unix endpoint must be an absolute socket path")
                return self
            if (
                url.scheme not in {"ws", "wss"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "Codex endpoint must be a ws/wss URL without credentials, query or fragment"
                )
            if url.scheme == "ws" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError(
                    "Remote Codex connections require wss; ws is restricted to loopback"
                )
            if url.scheme == "wss" and (
                not self.codex_auth_token or not self.codex_auth_token.get_secret_value().strip()
            ):
                raise ValueError("Remote Codex connections require DEVFEED_CODEX_AUTH_TOKEN")
        return self

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
