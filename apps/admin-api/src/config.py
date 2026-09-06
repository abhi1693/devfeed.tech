"""Configuration owned by the admin service, never loaded by the public API."""

from functools import lru_cache
from typing import Annotated, Literal

from devfeed_core.notification_config import ChimelySettings
from pydantic import Field, SecretStr, StringConstraints, model_validator
from pydantic_settings import SettingsConfigDict


class Settings(ChimelySettings):
    model_config = SettingsConfigDict(
        env_prefix="DEVFEED_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    # Unconfigured admin authentication denies access; there is no login bypass.
    admin_base_url: str | None = None
    chimely_admin_hmac_secret: SecretStr | None = None
    admin_required_role: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ] = "superuser"
    admin_cookie_secure: bool = True
    admin_session_ttl_seconds: int = Field(default=28800, ge=300, le=86400)
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
    oidc_roles_claim: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = (
        "urn:zitadel:iam:org:project:roles"
    )
    oidc_roles_format: Literal["organization_map", "string_list"] = "organization_map"
    oidc_role_scope_template: str | None = "urn:zitadel:iam:org:project:role:{role}"

    @model_validator(mode="after")
    def validate_admin_configuration(self):
        from urllib.parse import urlsplit

        if (
            self.notifications_enabled
            and self.chimely_admin_environment
            and (
                not self.chimely_admin_hmac_secret
                or self.chimely_admin_hmac_secret.get_secret_value().strip() in {"", "replace-me"}
            )
        ):
            raise ValueError("Admin inbox requires DEVFEED_CHIMELY_ADMIN_HMAC_SECRET")

        if any(char.isspace() for char in self.admin_required_role):
            raise ValueError("Admin role must be a single role key")
        if self.oidc_role_scope_template:
            try:
                role_scope = self.oidc_role_scope_template.format(role=self.admin_required_role)
            except (KeyError, ValueError, IndexError) as exc:
                raise ValueError("Invalid OIDC role scope template") from exc
            if any(char.isspace() for char in role_scope):
                raise ValueError("OIDC role scope must be a single scope")

        for name, value in (
            ("OIDC issuer", self.oidc_issuer_url),
            ("Admin base URL", self.admin_base_url),
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
            if name == "Admin base URL":
                if url.path not in {"", "/"}:
                    raise ValueError("Admin base URL must be an origin without a path")
                if url.scheme == "http" and self.admin_cookie_secure:
                    raise ValueError("HTTP admin development requires ADMIN_COOKIE_SECURE=false")
                if url.scheme == "https" and not self.admin_cookie_secure:
                    raise ValueError("HTTPS admin requires secure cookies")
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
