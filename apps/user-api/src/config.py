"""Independent user identity configuration; never falls back to admin credentials."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEVFEED_USER_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    base_url: str | None = None
    cookie_secure: bool = True
    session_ttl_seconds: int = Field(default=28800, ge=300, le=86400)
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_token_endpoint_auth_method: Literal[
        "none", "client_secret_basic", "client_secret_post"
    ] = "none"
    oidc_scopes: list[str] = ["openid", "profile", "email"]
    oidc_organization_id: str | None = None
    # Organization semantics are not standardized by OIDC. These defaults implement
    # Zitadel; another provider can change the scope template and exact claim name.
    oidc_organization_scope_template: str = "urn:zitadel:iam:org:id:{organization_id}"
    oidc_organization_claim: str = "urn:zitadel:iam:user:resourceowner:id"

    @field_validator(
        "base_url",
        "oidc_issuer_url",
        "oidc_client_id",
        "oidc_client_secret",
        "oidc_organization_id",
        mode="before",
    )
    @classmethod
    def empty_optional_user_setting(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_user_configuration(self):
        from urllib.parse import urlsplit

        for name, value in (
            ("OIDC issuer", self.oidc_issuer_url),
            ("User base URL", self.base_url),
        ):
            if value is None:
                continue
            url = urlsplit(value)
            if (
                url.scheme not in {"https", "http"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError(f"{name} must be an absolute URL without credentials or query")
            if (
                name == "OIDC issuer"
                and url.scheme == "http"
                and url.hostname not in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError("OIDC issuer requires HTTPS except on loopback")
            if name == "User base URL":
                if url.path not in {"", "/"}:
                    raise ValueError("User base URL must be an origin without a path")
                if url.scheme == "http" and self.cookie_secure:
                    raise ValueError("HTTP user development requires USER_COOKIE_SECURE=false")
                if url.scheme == "https" and not self.cookie_secure:
                    raise ValueError("HTTPS user requires secure cookies")
        if self.oidc_organization_id:
            if any(char.isspace() for char in self.oidc_organization_id):
                raise ValueError("OIDC organization ID cannot contain whitespace")
            try:
                scope = self.oidc_organization_scope_template.format(
                    organization_id=self.oidc_organization_id
                )
            except (KeyError, ValueError, IndexError) as exc:
                raise ValueError("Invalid OIDC organization scope template") from exc
            if (
                "{organization_id}" not in self.oidc_organization_scope_template
                or any(char.isspace() for char in scope)
                or not self.oidc_organization_claim.strip()
            ):
                raise ValueError("Organization authentication requires a scope template and claim")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
