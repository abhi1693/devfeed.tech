"""Public provider metadata, isolated from sessions and client credentials."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from devfeed_user_api import oidc
from devfeed_user_api.config import get_settings

router = APIRouter(tags=["discovery"])


class OIDCDiscoveryResponse(BaseModel):
    """Allowlisted public provider metadata; unknown and private claims stay private."""

    model_config = ConfigDict(strict=True, extra="ignore")

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    grant_types_supported: list[str]
    response_types_supported: list[str]
    scopes_supported: list[str] | None = None
    subject_types_supported: list[str] | None = None
    id_token_signing_alg_values_supported: list[str] | None = None
    token_endpoint_auth_methods_supported: list[str] | None = None
    code_challenge_methods_supported: list[str] | None = None
    userinfo_endpoint: str | None = None
    end_session_endpoint: str | None = None


@router.get(
    "/.well-known/openid-configuration",
    operation_id="reader_oidc_discovery",
    response_model=OIDCDiscoveryResponse,
)
def discovery() -> JSONResponse:
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
        public_metadata = OIDCDiscoveryResponse.model_validate(metadata)
    except (oidc.OIDCError, ValidationError):
        return JSONResponse({"detail": "Identity provider discovery is unavailable"}, 503, headers)
    return JSONResponse(public_metadata.model_dump(exclude_unset=True), headers=headers)
