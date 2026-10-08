"""Partner identity and revocable Redis sessions; no user accounts or passwords."""

import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Annotated, Literal, cast

from devfeed_http.schemas import OIDCCallbackQuery
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, Security
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyCookie
from pydantic import BaseModel
from redis.exceptions import RedisError

from devfeed_partner_api import oidc
from devfeed_partner_api.config import get_settings
from devfeed_partner_api.dependencies import get_redis

router = APIRouter(prefix="/v1/partner/auth", tags=["partner-auth"])
logger = logging.getLogger(__name__)
TOKEN = oidc.TOKEN
session_cookie = APIKeyCookie(
    name="__Host-devfeed_partner_session",
    auto_error=False,
    description="Partner session cookie (devfeed_partner_session in HTTP development).",
)


class AuthConfig(BaseModel):
    enabled: bool
    providers: list[Literal["github", "google"]]


class PartnerIdentity(BaseModel):
    subject: str
    issuer: str
    organization_id: str
    roles: list[str]
    name: str | None = None
    email: str | None = None
    expires_at: int
    csrf_token: str


def key(kind: str, token: str) -> str:
    return f"devfeed:partner:{kind}:{hashlib.sha256(token.encode()).hexdigest()}"


def require_config() -> None:
    if not oidc.configured(get_settings()):
        raise HTTPException(503, "Partner authentication is not configured")


def cookie(response: Response, kind: str, value: str, ttl: int) -> None:
    settings = get_settings()
    response.set_cookie(
        oidc.cookie_name(settings, kind),
        value,
        max_age=ttl,
        httponly=True,
        secure=settings.partner_cookie_secure,
        samesite="lax",
        path="/",
    )


def require_partner(
    request: Request,
    _cookie: Annotated[str | None, Security(session_cookie)] = None,
) -> PartnerIdentity:
    require_config()
    settings = get_settings()
    token = request.cookies.get(oidc.cookie_name(settings, "session"), "")
    if not TOKEN.fullmatch(token):
        raise HTTPException(401, "Partner sign-in required")
    try:
        raw = cast(bytes | None, get_redis().get(key("session", token)))
    except RedisError as exc:
        raise HTTPException(503, "Partner sessions temporarily unavailable") from exc
    if raw is None:
        raise HTTPException(401, "Partner session expired")
    try:
        record = json.loads(raw)
        partner = PartnerIdentity.model_validate(record)
        if partner.expires_at <= time.time() or record.get("policy") != oidc.policy_key(settings):
            raise ValueError("Expired or invalidated session")
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(401, "Partner session expired") from exc
    if "partner" not in partner.roles:
        raise HTTPException(403, "Required partner role is not granted")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("x-csrf-token", "")
        if (
            request.headers.get("origin") != str(settings.partner_base_url).rstrip("/")
            or not TOKEN.fullmatch(supplied)
            or not hmac.compare_digest(supplied, partner.csrf_token)
        ):
            raise HTTPException(403, "Invalid request origin or CSRF token")
    return partner


Partner = Annotated[PartnerIdentity, Depends(require_partner)]


def actor(partner: PartnerIdentity) -> dict[str, str]:
    identity = {key: getattr(partner, key) for key in ("subject", "issuer", "organization_id")}
    for field in ("name", "email"):
        value = getattr(partner, field)
        if value and value.strip():
            identity[field] = value.strip()
    return identity


@router.get("/config", response_model=AuthConfig, operation_id="partner_auth_config")
def config():
    settings = get_settings()
    providers: list[Literal["github", "google"]] = []
    if oidc.configured(settings):
        if settings.oidc_github_idp_id:
            providers.append("github")
        if settings.oidc_google_idp_id:
            providers.append("google")
    return AuthConfig(enabled=oidc.configured(settings), providers=providers)


@router.get(
    "/login", operation_id="partner_auth_login", response_class=RedirectResponse, status_code=302
)
def login(
    request: Request,
    reauthenticate: bool = False,
    provider: Literal["github", "google"] | None = None,
):
    require_config()
    settings = get_settings()
    try:
        metadata = oidc.discovery(settings)
        provider_id = getattr(settings, f"oidc_{provider}_idp_id") if provider is not None else None
        if provider is not None and not provider_id:
            raise oidc.OIDCError("Identity provider is not configured")
        location, flow = oidc.start(
            settings, metadata, reauthenticate=reauthenticate, identity_provider_id=provider_id
        )
        get_redis().set(key("flow", flow["state"]), json.dumps(flow), ex=oidc.FLOW_TTL)
    except (oidc.OIDCError, RedisError) as exc:
        logger.warning("partner_login_unavailable", extra={"error_type": type(exc).__name__})
        raise HTTPException(503, "Partner sign-in temporarily unavailable") from exc
    response = RedirectResponse(location, status_code=302)
    # Each browser can have one outstanding flow. A subsequent login supersedes it.
    cookie(response, "state", flow["browser"], oidc.FLOW_TTL)
    return response


@router.get(
    "/callback",
    operation_id="partner_auth_callback",
    response_class=RedirectResponse,
    status_code=302,
)
def callback(request: Request, params: Annotated[OIDCCallbackQuery, Query()]) -> RedirectResponse:
    require_config()
    settings = get_settings()
    failure = "login_failed"
    bound_flow = False
    try:
        state = params.state or ""
        browser = request.cookies.get(oidc.cookie_name(settings, "state"), "")
        redis = get_redis()
        flow = oidc.consume_callback_flow(
            query_params=request.query_params,
            state=state,
            browser=browser,
            redis=redis,
            flow_key=key("flow", state),
            policy=oidc.policy_key(settings),
        )
        bound_flow = True
        # A browser-validated sign-in replaces its previous local identity,
        # even if the new identity fails authorization. Never do this for an
        # unbound/replayed callback: that would allow forced logout by URL.
        previous = request.cookies.get(oidc.cookie_name(settings, "session"), "")
        if TOKEN.fullmatch(previous):
            redis.delete(key("session", previous))
        code = oidc.validate_callback_response(
            code=params.code,
            provider_error=params.error,
            issuer=params.iss,
            expected_issuer=settings.oidc_issuer_url,
        )
        partner = oidc.identity(settings, oidc.discovery(settings), flow, code)
        if "partner" not in partner["roles"]:
            failure = "access_denied"
            raise oidc.OIDCError("Required partner role is not granted")
        ttl = partner["expires_at"] - int(time.time())
        if ttl <= 0:
            raise oidc.OIDCError("Expired identity")
        token = secrets.token_urlsafe(32)
        redis.set(key("session", token), json.dumps(partner), ex=ttl)
        response = RedirectResponse(str(settings.partner_base_url).rstrip("/") + "/", 302)
        cookie(response, "session", token, ttl)
        logger.info("partner_signed_in")
    except (oidc.OIDCError, RedisError, ValueError, KeyError, TypeError) as exc:
        logger.warning("partner_login_failed", extra={"error_type": type(exc).__name__})
        response = RedirectResponse(
            str(settings.partner_base_url).rstrip("/") + f"/login?error={failure}", 302
        )
        if bound_flow:
            cookie(response, "session", "", 0)
    cookie(response, "state", "", 0)
    return response


@router.get("/me", response_model=PartnerIdentity, operation_id="partner_auth_me")
def me(partner: Partner):
    return partner


@router.post(
    "/logout",
    status_code=204,
    operation_id="partner_auth_logout",
    response_class=Response,
    response_model=None,
)
def logout(
    request: Request,
    _cookie: Annotated[str | None, Security(session_cookie)] = None,
) -> Response:
    # Revoking this browser's session must not require a current partner grant or
    # policy. In particular, revoked/expired sessions can still be signed out.
    settings = get_settings()
    if not settings.partner_base_url:
        raise HTTPException(503, "Partner origin is not configured")
    if request.headers.get("origin") != str(settings.partner_base_url).rstrip("/"):
        raise HTTPException(403, "Invalid request origin")
    token = request.cookies.get(oidc.cookie_name(settings, "session"), "")
    if TOKEN.fullmatch(token):
        try:
            redis = get_redis()
            session_key = key("session", token)
            raw = cast(bytes | None, redis.get(session_key))
            if raw is not None:
                # Validate CSRF against the existing record even when the role,
                # expiry, or configured access policy no longer permits access.
                try:
                    expected = json.loads(raw)["csrf_token"]
                except (ValueError, TypeError, KeyError) as exc:
                    raise HTTPException(401, "Invalid partner session") from exc
                supplied = request.headers.get("x-csrf-token", "")
                if (
                    not isinstance(expected, str)
                    or not TOKEN.fullmatch(expected)
                    or not TOKEN.fullmatch(supplied)
                    or not hmac.compare_digest(supplied, expected)
                ):
                    raise HTTPException(403, "Invalid CSRF token")
            redis.delete(session_key)
        except RedisError as exc:
            # Keep the browser session until revocation succeeds; do not claim
            # success while a replayable server-side session might still exist.
            raise HTTPException(503, "Could not revoke session; retry sign-out") from exc
    response = Response(status_code=204)
    cookie(response, "session", "", 0)
    cookie(response, "state", "", 0)
    logger.info("partner_signed_out")
    return response
