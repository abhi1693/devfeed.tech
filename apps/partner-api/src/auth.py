"""Partner identity and revocable Redis sessions; shared verified user records and no passwords."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Annotated, Literal, cast

from devfeed_core.identity_accounts import register_verified_user
from devfeed_http import oidc_logout, oidc_retry
from devfeed_http.schemas import OIDCCallbackQuery
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, Security
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyCookie
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from devfeed_partner_api import oidc
from devfeed_partner_api.config import Settings, get_settings
from devfeed_partner_api.dependencies import get_redis

router = APIRouter(prefix="/v1/partner/auth", tags=["partner-auth"])
logger = logging.getLogger(__name__)
TOKEN = oidc.TOKEN
IDENTITY_REGISTRATION_VERSION = 1
# Update an existing session without extending its lifetime or restoring a session
# removed by logout while the legacy identity backfill was using the database.
REGISTERED_SESSION = """
local current = redis.call('GET', KEYS[1])
if not current then return 0 end
if cjson.decode(current).identity_registration_version == tonumber(ARGV[3]) then return 1 end
if current ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL')
return 1
"""
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


def _recovery(request: Request, settings: Settings) -> oidc_retry.Recovery:
    return oidc_retry.Recovery(
        base_url=str(settings.partner_base_url),
        browser=request.cookies.get(oidc.cookie_name(settings, "state"), ""),
        policy=oidc.policy_key(settings),
        key=key,
        cookie=cookie,
        redis=get_redis,
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
        if not oidc_logout.active(get_redis(), "partner", settings, record):
            raise ValueError("Provider session has ended")
        partner = PartnerIdentity.model_validate(record)
        if partner.expires_at <= time.time() or record.get("policy") != oidc.policy_key(settings):
            raise ValueError("Expired or invalidated session")
    except RedisError as exc:
        raise HTTPException(503, "Partner sessions temporarily unavailable") from exc
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
    request.state.partner_session = (key("session", token), raw, record)
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
def config() -> AuthConfig:
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
    retry: str | None = None,
) -> RedirectResponse:
    require_config()
    settings = get_settings()
    recovery = _recovery(request, settings)
    recovered = False
    try:
        if recovery.consume(request, retry) is not None:
            recovered = True
            reauthenticate = True
        metadata = oidc.discovery(settings)
        provider_id = getattr(settings, f"oidc_{provider}_idp_id") if provider is not None else None
        if provider is not None and not provider_id:
            raise oidc.OIDCError("Identity provider is not configured")
        location, flow = oidc.start(
            settings, metadata, reauthenticate=reauthenticate, identity_provider_id=provider_id
        )
        get_redis().set(key("flow", flow["state"]), json.dumps(flow), ex=oidc.FLOW_TTL)
    except oidc_retry.ExpiredRetry:
        return recovery.response("login_expired")
    except (oidc.OIDCError, RedisError) as exc:
        logger.warning("partner_login_unavailable", extra={"error_type": type(exc).__name__})
        if recovered:
            return recovery.unavailable()
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
    recovery = _recovery(request, settings)
    redis = None
    try:
        state = params.state or ""
        redis = get_redis()
        flow = oidc.consume_callback_flow(
            query_params=request.query_params,
            state=state,
            browser=recovery.browser,
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
        register_verified_user(partner)
        partner["identity_registration_version"] = IDENTITY_REGISTRATION_VERSION
        token = secrets.token_urlsafe(32)
        oidc_logout.save(redis, "partner", settings, key("session", token), partner, ttl)
        response = RedirectResponse(str(settings.partner_base_url).rstrip("/") + "/", 302)
        cookie(response, "session", token, ttl)
        logger.info("partner_signed_in")
    except (oidc.OIDCError, RedisError, SQLAlchemyError, ValueError, KeyError, TypeError) as exc:
        return recovery.failed_callback(
            exc,
            redis=redis,
            logger=logger,
            event="partner_login_failed",
            bound_flow=bound_flow,
            reason=failure,
        )
    if bound_flow:
        cookie(response, "state", "", 0)
    return response


@router.get("/me", response_model=PartnerIdentity, operation_id="partner_auth_me")
def me(request: Request, partner: Partner) -> PartnerIdentity:
    session_key, raw, record = request.state.partner_session
    if record.get("identity_registration_version") == IDENTITY_REGISTRATION_VERSION:
        return partner
    # Backfill pre-existing sessions once. Registration materializes a verified
    # identity; current account membership is still checked on every account read.
    try:
        register_verified_user(partner.model_dump())
        registered = {**record, "identity_registration_version": IDENTITY_REGISTRATION_VERSION}
        if not get_redis().eval(
            REGISTERED_SESSION,
            1,
            session_key,
            raw,
            json.dumps(registered),
            IDENTITY_REGISTRATION_VERSION,
        ):
            raise HTTPException(503, "Partner session changed; retry")
    except (SQLAlchemyError, RedisError) as exc:
        raise HTTPException(503, "Could not register partner user; retry") from exc
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


oidc_logout.add_routes(
    router,
    service="partner",
    get_settings=lambda: get_settings(),
    get_redis=lambda: get_redis(),
    local_logout=logout,
    cookie=cookie,
)
