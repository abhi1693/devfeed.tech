"""Independent user identity configuration; never falls back to admin credentials."""

import re
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEVFEED_USER_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    mcp_session_ttl_seconds: int = Field(default=30 * 86400, ge=3600, le=90 * 86400)
    mcp_session_absolute_ttl_seconds: int = Field(default=90 * 86400, ge=3600, le=365 * 86400)
    mcp_resource_url: str | None = None
    mcp_issuer_url: str | None = None
    base_url: str | None = None
    x_pixel_enabled: bool = Field(default=False, validation_alias="DEVFEED_X_PIXEL_ENABLED")
    x_pixel_token: SecretStr | None = Field(default=None, validation_alias="X_PIXEL_TOKEN")
    x_signup_event_id: str | None = Field(default=None, validation_alias="X_SIGNUP_EVENT_ID")
    cookie_secure: bool = True
    session_ttl_seconds: int = Field(default=30 * 86400, ge=300, le=90 * 86400)
    session_absolute_ttl_seconds: int = Field(default=90 * 86400, ge=300, le=90 * 86400)
    extension_ids: list[str] = Field(default_factory=list)
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    # Identity provider IDs are shared with admin; application clients remain separate.
    oidc_github_idp_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DEVFEED_OIDC_GITHUB_IDP_ID", "oidc_github_idp_id"),
    )
    oidc_google_idp_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DEVFEED_OIDC_GOOGLE_IDP_ID", "oidc_google_idp_id"),
    )
    oidc_token_endpoint_auth_method: Literal[
        "none", "client_secret_basic", "client_secret_post"
    ] = "none"
    oidc_scopes: list[str] = ["openid", "profile", "email"]
    oidc_organization_id: str | None = None
    # Organization semantics are not standardized by OIDC. These defaults implement
    # Zitadel; another provider can change the scope template and exact claim name.
    oidc_organization_scope_template: str = "urn:zitadel:iam:org:id:{organization_id}"
    oidc_organization_claim: str = "urn:zitadel:iam:user:resourceowner:id"

    @field_validator("extension_ids")
    @classmethod
    def validate_extension_ids(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[a-p]{32}", extension_id) for extension_id in value):
            raise ValueError("Extension IDs must be exact 32-character Chrome extension IDs")
        return value

    def allows_request_origin(self, origin: str | None) -> bool:
        if not origin:
            return False
        return bool(self.base_url and origin == self.base_url.rstrip("/")) or any(
            origin == f"chrome-extension://{extension_id}" for extension_id in self.extension_ids
        )

    @field_validator(
        "base_url",
        "x_pixel_token",
        "x_signup_event_id",
        "mcp_resource_url",
        "mcp_issuer_url",
        "oidc_issuer_url",
        "oidc_client_id",
        "oidc_client_secret",
        "oidc_github_idp_id",
        "oidc_google_idp_id",
        "oidc_organization_id",
        mode="before",
    )
    @classmethod
    def empty_optional_user_setting(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("x_signup_event_id")
    @classmethod
    def validate_x_signup_event_id(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"tw-pc5f8-[a-zA-Z0-9]+", value):
            raise ValueError("X sign-up event ID must belong to pixel pc5f8")
        return value

    @field_validator("x_pixel_enabled", mode="before")
    @classmethod
    def validate_x_pixel_enabled(cls, value):
        return value.strip().lower() == "true" if isinstance(value, str) else value

    @field_validator("oidc_github_idp_id", "oidc_google_idp_id")
    @classmethod
    def validate_identity_provider_id(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise ValueError("OIDC identity provider IDs must be valid provider identifiers")
        return value

    @model_validator(mode="after")
    def validate_user_configuration(self):
        from urllib.parse import urlsplit

        if self.mcp_session_ttl_seconds > self.mcp_session_absolute_ttl_seconds:
            raise ValueError("MCP inactivity timeout cannot exceed its absolute lifetime")
        if bool(self.mcp_resource_url) != bool(self.mcp_issuer_url):
            raise ValueError("MCP resource and issuer URLs must be configured together")
        for name, value in (
            ("MCP resource", self.mcp_resource_url),
            ("MCP issuer", self.mcp_issuer_url),
        ):
            if value:
                url = urlsplit(value)
                if (
                    not url.hostname
                    or url.username
                    or url.password
                    or url.query
                    or url.fragment
                    or url.scheme not in {"http", "https"}
                    or (
                        url.scheme == "http"
                        and url.hostname not in {"localhost", "127.0.0.1", "::1"}
                    )
                ):
                    raise ValueError(
                        f"{name} must use HTTPS (or HTTP loopback) without credentials"
                    )
        if self.mcp_issuer_url and urlsplit(self.mcp_issuer_url).path not in {"", "/"}:
            raise ValueError("MCP issuer must be an origin")
        if self.session_ttl_seconds > self.session_absolute_ttl_seconds:
            raise ValueError("User session inactivity timeout cannot exceed its absolute lifetime")

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
