"""Public provider metadata, isolated from sessions and client credentials."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from devfeed_user_api import oidc
from devfeed_user_api.config import get_settings

router = APIRouter(tags=["discovery"])
PUBLIC_FIELDS = {
    "issuer",
    "authorization_endpoint",
    "token_endpoint",
    "jwks_uri",
    "grant_types_supported",
    "response_types_supported",
    "scopes_supported",
    "subject_types_supported",
    "id_token_signing_alg_values_supported",
    "token_endpoint_auth_methods_supported",
    "code_challenge_methods_supported",
    "userinfo_endpoint",
    "end_session_endpoint",
}


@router.get("/.well-known/openid-configuration", operation_id="reader_oidc_discovery")
def discovery():
    settings = get_settings()
    headers = {"Cache-Control": "no-store"}
    if not oidc.configured(settings):
        return JSONResponse({"detail": "Reader authentication is not configured"}, 404, headers)
    try:
        metadata = oidc.discovery(settings)
        for field in ("grant_types_supported", "response_types_supported"):
            value = metadata.get(field)
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) and item for item in value)
            ):
                raise oidc.OIDCError("Invalid discovery capabilities")
    except oidc.OIDCError:
        return JSONResponse({"detail": "Identity provider discovery is unavailable"}, 503, headers)
    return JSONResponse(
        {key: value for key, value in metadata.items() if key in PUBLIC_FIELDS}, headers=headers
    )
