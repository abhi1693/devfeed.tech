"""Partner OIDC policy using the shared, validated authorization-code protocol."""

import hashlib
import json
import secrets

from devfeed_http import oidc as protocol
from devfeed_http.oidc import FLOW_TTL as FLOW_TTL
from devfeed_http.oidc import TOKEN as TOKEN
from devfeed_http.oidc import OIDCError as OIDCError
from devfeed_http.oidc import consume_callback_flow as consume_callback_flow
from devfeed_http.oidc import discovery as discovery
from devfeed_http.oidc import validate_callback_response as validate_callback_response

from devfeed_partner_api.config import Settings
from devfeed_partner_api.roles import verified_roles


def configured(settings: Settings) -> bool:
    return protocol.configured(
        settings, base_url=str(settings.partner_base_url) if settings.partner_base_url else None
    )


def policy_key(settings: Settings) -> str:
    """Invalidate in-flight logins and sessions after issuer/client/access-policy changes."""
    values = {
        key: value
        for key, value in settings.model_dump(mode="json").items()
        if key.startswith(("oidc_", "partner_"))
    }
    if settings.oidc_client_secret:
        values["oidc_client_secret"] = settings.oidc_client_secret.get_secret_value()
    values["authorization_policy_version"] = "organization-partner-role-v2"
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def redirect_uri(settings: Settings) -> str:
    return f"{str(settings.partner_base_url).rstrip('/')}/api/v1/partner/auth/callback"


def cookie_name(settings: Settings, kind: str) -> str:
    return protocol.cookie_name("partner", kind, secure=settings.partner_cookie_secure)


def start(
    settings: Settings,
    metadata: dict,
    *,
    reauthenticate: bool = False,
    identity_provider_id: str | None = None,
) -> tuple[str, dict]:
    scope = settings.oidc_role_scope_template
    return protocol.start(
        settings,
        metadata,
        redirect_uri=redirect_uri(settings),
        policy=policy_key(settings),
        reauthenticate=reauthenticate,
        identity_provider_id=identity_provider_id,
        role_scope=scope.format(role="partner") if scope else None,
    )


def identity(settings: Settings, metadata: dict, flow: dict, code: str) -> dict:
    result = protocol.identity(
        settings,
        metadata,
        flow,
        code,
        redirect_uri=redirect_uri(settings),
        session_ttl=settings.partner_session_ttl_seconds,
    )
    result["roles"] = verified_roles(
        settings, result.pop("id_claims"), result.pop("userinfo_claims")
    )
    result["policy"] = policy_key(settings)
    result["csrf_token"] = secrets.token_urlsafe(32)
    return result
