"""User OIDC policy using the shared, validated authorization-code protocol."""

import hashlib
import json
import secrets

from devfeed_http import oidc as protocol
from devfeed_http.oidc import FLOW_TTL as FLOW_TTL
from devfeed_http.oidc import OIDCError as OIDCError
from devfeed_http.oidc import discovery as discovery

from devfeed_user_api.config import Settings


def configured(settings: Settings) -> bool:
    return bool(
        settings.base_url
        and settings.oidc_issuer_url
        and settings.oidc_client_id
        and settings.oidc_organization_id
        and (
            settings.oidc_token_endpoint_auth_method == "none"
            or (settings.oidc_client_secret and settings.oidc_client_secret.get_secret_value())
        )
    )


def policy_key(settings: Settings) -> str:
    """Invalidate in-flight logins and sessions after issuer/client/access-policy changes."""
    values = {
        key: value
        for key, value in settings.model_dump(mode="json").items()
        if key.startswith("oidc_") or key in {"base_url", "cookie_secure", "session_ttl_seconds"}
    }
    if settings.oidc_client_secret:
        values["oidc_client_secret"] = settings.oidc_client_secret.get_secret_value()
    values["authorization_policy_version"] = "user-organization-v1"
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def redirect_uri(settings: Settings) -> str:
    return f"{str(settings.base_url).rstrip('/')}/api/v1/user/auth/callback"


def cookie_name(settings: Settings, kind: str) -> str:
    prefix = "__Host-" if settings.cookie_secure else ""
    return f"{prefix}devfeed_user_{kind}"


def start(settings: Settings, metadata: dict, *, register: bool = False) -> tuple[str, dict]:
    return protocol.start(
        settings,
        metadata,
        redirect_uri=redirect_uri(settings),
        policy=policy_key(settings),
        register=register,
    )


def identity(settings: Settings, metadata: dict, flow: dict, code: str) -> dict:
    result = protocol.identity(
        settings,
        metadata,
        flow,
        code,
        redirect_uri=redirect_uri(settings),
        session_ttl=settings.session_ttl_seconds,
    )
    result.pop("id_claims")
    result.pop("userinfo_claims")
    result["policy"] = policy_key(settings)
    result["csrf_token"] = secrets.token_urlsafe(32)
    return result
