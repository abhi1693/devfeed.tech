from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEVFEED_MCP_", extra="ignore")

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
