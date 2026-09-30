from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEVFEED_MCP_", extra="ignore", hide_input_in_errors=True
    )

    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    user_api_url: str | None = None
    oauth_issuer_url: str | None = None
    public_url: str | None = None
    web_url: str | None = None
    redis_url: SecretStr | None = None

    @field_validator(
        "user_api_url",
        "oauth_issuer_url",
        "public_url",
        "web_url",
        "redis_url",
        mode="before",
    )
    @classmethod
    def empty_optional(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_personal_settings(self):
        values = [
            self.user_api_url,
            self.oauth_issuer_url,
            self.public_url,
            self.web_url,
            self.redis_url,
        ]
        if any([self.user_api_url, self.oauth_issuer_url, self.redis_url]) and not all(values):
            raise ValueError(
                "Personal MCP requires user API, OAuth issuer, public URL, "
                "web URL and Redis settings"
            )
        for value in [
            self.user_api_url,
            self.oauth_issuer_url,
            self.public_url,
            self.web_url,
        ]:
            if value:
                parts = urlsplit(value)
                if (
                    parts.scheme not in {"http", "https"}
                    or not parts.hostname
                    or parts.username
                    or parts.password
                    or parts.query
                    or parts.fragment
                ):
                    raise ValueError(
                        "Personal MCP URLs must be HTTP(S) without credentials, query or fragment"
                    )
                local_consent = (
                    value == self.web_url
                    and self.oauth_issuer_url
                    and urlsplit(self.oauth_issuer_url).hostname
                    in {"localhost", "127.0.0.1", "::1"}
                )
                if (
                    parts.scheme == "http"
                    and not local_consent
                    and parts.hostname
                    not in {
                        "localhost",
                        "127.0.0.1",
                        "::1",
                        "user-api",
                    }
                ):
                    raise ValueError("Remote personal MCP URLs require HTTPS")
        if self.oauth_issuer_url and urlsplit(self.oauth_issuer_url).path not in {"", "/"}:
            raise ValueError("OAuth issuer must be an origin; ingress must serve its OAuth routes")
        if self.user_api_url and urlsplit(self.user_api_url).path not in {"", "/"}:
            raise ValueError("User API URL must be a service origin")
        return self

    api_url: str = "http://127.0.0.1:8000"
    timeout_seconds: float = Field(default=10, gt=0, le=30)
    max_connections: int = Field(default=16, ge=1, le=128)
    max_response_bytes: int = Field(default=2_000_000, ge=1024, le=5_000_000)
    allowed_hosts: list[str] = [
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
    ]
    allowed_origins: list[str] = []

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
            or any(ord(char) < 33 or ord(char) == 127 for char in value)
        ):
            raise ValueError("Use the direct public API origin without credentials or a path")
        _ = parts.port
        return value.rstrip("/")
