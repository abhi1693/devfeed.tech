"""Optional user identity and isolated revocable browser sessions."""

import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import UTC, datetime
from typing import Annotated, Literal, cast

from devfeed_http import oidc_logout, oidc_retry
from devfeed_http.operation_policies import ExtensionTransportPolicy, ReaderGatewayPolicy
from devfeed_http.reader_destinations import (
    parse_reader_destination,
    serialize_reader_destination,
)
from devfeed_http.schemas import OIDCCallbackQuery
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
)
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyCookie
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from devfeed_user_api import oidc
from devfeed_user_api.accounts import save_user_with_status, touch_user_activity
from devfeed_user_api.config import get_settings
from devfeed_user_api.dependencies import get_redis
from devfeed_user_api.x_attribution import click_id, valid_click_id
from devfeed_user_api.x_conversions import send_signup_conversion

router = APIRouter(prefix="/v1/user/auth", tags=["user-auth"])
logger = logging.getLogger(__name__)
TOKEN = oidc.TOKEN
# Compare-and-set prevents renewal racing logout from resurrecting a session.
# Concurrent tabs use the winning record without shortening its lifetime.
RENEW_SESSION = """
local current = redis.call('GET', KEYS[1])
if not current then return false end
if current ~= ARGV[1] then return current end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return ARGV[2]
"""
session_cookie = APIKeyCookie(
    name="__Host-devfeed_user_session",
    auto_error=False,
    description="User session cookie (devfeed_user_session in HTTP development).",
)


class AuthConfig(BaseModel):
    enabled: bool
    providers: list[Literal["github", "google"]]


class UserIdentity(BaseModel):
    subject: str
    issuer: str
    organization_id: str
    user_id: str
    name: str | None = None
    email: str | None = None
    expires_at: int
    csrf_token: str


def key(kind: str, token: str) -> str:
    return f"devfeed:user:{kind}:{hashlib.sha256(token.encode()).hexdigest()}"


def require_config() -> None:
    if not oidc.configured(get_settings()):
        raise HTTPException(503, "User authentication is not configured")


def cookie(response: Response, kind: str, value: str, ttl: int) -> None:
    if value and not TOKEN.fullmatch(value):
        raise ValueError("Invalid authentication cookie token")
    settings = get_settings()
    response.set_cookie(
        oidc.cookie_name(settings, kind),
        value,
        max_age=ttl,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def require_user(
    request: Request,
    _cookie: Annotated[str | None, Security(session_cookie)] = None,
) -> UserIdentity:
    if request.headers.get("authorization"):
        require_config()
        from devfeed_user_api.mcp import require_agent_user

        return require_agent_user(request)
    return require_browser_user(request, _cookie)


def require_browser_user(
    request: Request,
    _cookie: Annotated[str | None, Security(session_cookie)] = None,
) -> UserIdentity:
    require_config()
    settings = get_settings()
    token = request.cookies.get(oidc.cookie_name(settings, "session"), "")
    if not TOKEN.fullmatch(token):
        raise HTTPException(401, "User sign-in required")
    try:
        raw = cast(bytes | None, get_redis().get(key("session", token)))
    except RedisError as exc:
        raise HTTPException(503, "User sessions temporarily unavailable") from exc
    if raw is None:
        raise HTTPException(401, "User session expired")
    try:
        record = json.loads(raw)
        if not oidc_logout.active(get_redis(), "user", settings, record):
            raise ValueError("Provider session has ended")
        user = UserIdentity.model_validate(record)
        if (
            user.expires_at <= time.time()
            or record["absolute_expires_at"] <= time.time()
            or record.get("policy") != oidc.policy_key(settings)
        ):
            raise ValueError("Expired or invalidated session")
    except RedisError as exc:
        raise HTTPException(503, "User sessions temporarily unavailable") from exc
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise HTTPException(401, "User session expired") from exc
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("x-csrf-token", "")
        if (
            not settings.allows_request_origin(request.headers.get("origin"))
            or not TOKEN.fullmatch(supplied)
            or not hmac.compare_digest(supplied, user.csrf_token)
        ):
            raise HTTPException(403, "Invalid request origin or CSRF token")
    _record_activity(user)
    return user


def _record_activity(user: UserIdentity) -> None:
    """Debounce durable activity writes while keeping the auth path best-effort."""
    activity_key = key("activity", user.user_id)
    try:
        redis = get_redis()
        if redis.get(activity_key) is None:
            redis.set(activity_key, "1", ex=900)
            touch_user_activity(user.user_id)
    except RedisError:
        logger.warning("user_activity_marker_unavailable")


User = Annotated[UserIdentity, Depends(require_user)]
BrowserUser = Annotated[UserIdentity, Depends(require_browser_user)]


@router.get(
    "/config",
    response_model=AuthConfig,
    operation_id="user_auth_config",
    openapi_extra=ReaderGatewayPolicy(extension=ExtensionTransportPolicy()).openapi_extra(),
)
def config():
    settings = get_settings()
    providers = [
        provider
        for provider, identity_provider_id in (
            ("github", settings.oidc_github_idp_id),
            ("google", settings.oidc_google_idp_id),
        )
        if identity_provider_id
    ]
    return AuthConfig(enabled=oidc.configured(settings), providers=providers)


@router.get(
    "/login",
    operation_id="user_auth_login",
    response_class=RedirectResponse,
    status_code=302,
    openapi_extra=ReaderGatewayPolicy(cookie_policy="reader-attribution").openapi_extra(),
)
def login(
    request: Request,
    register: bool = False,
    return_to: str = "/",
    provider: Literal["github", "google"] | None = None,
    retry: str | None = None,
):
    destination = parse_reader_destination(return_to)
    if destination is None:
        raise HTTPException(422, "Invalid sign-in destination")
    destination_record = destination.as_record()
    require_config()
    settings = get_settings()
    recovery = oidc_retry.Recovery(
        base_url=str(settings.base_url),
        browser=request.cookies.get(oidc.cookie_name(settings, "state"), ""),
        policy=oidc.policy_key(settings),
        key=key,
        cookie=cookie,
        redis=get_redis,
    )
    recovered = False
    try:
        context = recovery.consume(request, retry)
        if context is not None:
            recovered = True
            destination = parse_reader_destination(
                serialize_reader_destination(context.get("destination")) or ""
            )
            if destination is None:
                raise oidc_retry.ExpiredRetry()
            destination_record = destination.as_record()
            register = False
        metadata = oidc.discovery(settings)
        provider_id = getattr(settings, f"oidc_{provider}_idp_id") if provider is not None else None
        if provider is not None and not provider_id:
            raise oidc.OIDCError("Identity provider is not configured")
        location, flow = oidc.start(
            settings, metadata, register=register, identity_provider_id=provider_id
        )
        flow["destination"] = destination_record
        if settings.x_pixel_enabled:
            captured = click_id(request.cookies, secure=settings.cookie_secure)
            if captured:
                flow["x_click_id"] = captured
        get_redis().set(key("flow", flow["state"]), json.dumps(flow), ex=oidc.FLOW_TTL)
    except oidc_retry.ExpiredRetry:
        return recovery.response("login_expired")
    except (oidc.OIDCError, RedisError) as exc:
        logger.warning("user_login_unavailable", extra={"error_type": type(exc).__name__})
        if recovered:
            return recovery.unavailable(destination=destination_record)
        raise HTTPException(503, "User sign-in temporarily unavailable") from exc
    response = RedirectResponse(location, status_code=302)
    # Each browser can have one outstanding flow. A subsequent login supersedes it.
    cookie(response, "state", flow["browser"], oidc.FLOW_TTL)
    return response


@router.get(
    "/callback", operation_id="user_auth_callback", response_class=RedirectResponse, status_code=302
)
def callback(
    request: Request,
    params: Annotated[OIDCCallbackQuery, Query()],
    background_tasks: BackgroundTasks,
) -> RedirectResponse:
    require_config()
    settings = get_settings()
    failure = "login_failed"
    bound_flow = False
    recovery = oidc_retry.Recovery(
        base_url=str(settings.base_url),
        browser=request.cookies.get(oidc.cookie_name(settings, "state"), ""),
        policy=oidc.policy_key(settings),
        key=key,
        cookie=cookie,
        redis=get_redis,
    )
    redis = None
    retry_destination = None
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
        destination = serialize_reader_destination(flow.get("destination"))
        if destination is None:
            raise oidc.OIDCError("Invalid sign-in destination")
        retry_destination = flow["destination"]
        # A browser-validated sign-in replaces its previous local identity,
        # even if the new identity fails validation. Never do this for an
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
        user = oidc.identity(settings, oidc.discovery(settings), flow, code)
        ttl = user["expires_at"] - int(time.time())
        if ttl <= 0:
            raise oidc.OIDCError("Expired identity")
        user["user_id"], created = save_user_with_status(user)
        token = secrets.token_urlsafe(32)
        oidc_logout.save(redis, "user", settings, key("session", token), user, ttl)
        response = RedirectResponse(str(settings.base_url).rstrip("/") + destination, 302)
        # The browser retains the server-issued token only until the fixed absolute
        # deadline. Redis independently enforces and renews the shorter idle window.
        cookie(response, "session", token, user["absolute_expires_at"] - int(time.time()))
        logger.info("user_signed_in")
        if created:
            background_tasks.add_task(
                send_signup_conversion,
                settings,
                user_id=user["user_id"],
                email=user.get("email"),
                twclid=valid_click_id(flow.get("x_click_id")) if settings.x_pixel_enabled else None,
                conversion_time=datetime.now(UTC),
            )
    except (oidc.OIDCError, RedisError, SQLAlchemyError, ValueError, KeyError, TypeError) as exc:
        return recovery.failed_callback(
            exc,
            redis=redis,
            logger=logger,
            event="user_login_failed",
            bound_flow=bound_flow,
            reason=failure,
            destination=retry_destination,
            retry_allowed=retry_destination is not None,
            dependency_errors=(SQLAlchemyError,),
        )
    if bound_flow:
        cookie(response, "state", "", 0)
    return response


@router.get(
    "/me",
    response_model=UserIdentity | None,
    operation_id="user_auth_me",
    openapi_extra=ReaderGatewayPolicy(extension=ExtensionTransportPolicy()).openapi_extra(),
)
def me(request: Request):
    # Anonymous browsing is normal; protected routes still use require_user.
    try:
        require_user(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return None
        raise
    settings = get_settings()
    token = request.cookies[oidc.cookie_name(settings, "session")]
    now = int(time.time())
    try:
        redis = get_redis()
        raw = redis.get(key("session", token))
        if raw is None:
            return None
        record = json.loads(raw)
        absolute = record["absolute_expires_at"]
        if absolute <= now or record["expires_at"] <= now:
            return None
        if record["renewed_at"] <= now - min(3600, settings.session_ttl_seconds // 2):
            record["expires_at"] = min(now + settings.session_ttl_seconds, absolute)
            record["renewed_at"] = now
            raw = redis.eval(
                RENEW_SESSION,
                1,
                key("session", token),
                raw,
                json.dumps(record),
                record["expires_at"] - now,
            )
            if not raw:
                return None
            record = json.loads(raw)
        if not oidc_logout.active(redis, "user", settings, record):
            return None
        user = UserIdentity.model_validate(record)
        if user.expires_at <= now or record.get("policy") != oidc.policy_key(settings):
            return None
        return user
    except RedisError as exc:
        raise HTTPException(503, "User sessions temporarily unavailable") from exc
    except (ValueError, TypeError, KeyError):
        return None


@router.post(
    "/logout",
    status_code=204,
    operation_id="user_auth_logout",
    response_class=Response,
    response_model=None,
    openapi_extra=ReaderGatewayPolicy(extension=ExtensionTransportPolicy()).openapi_extra(),
)
def logout(
    request: Request,
    _cookie: Annotated[str | None, Security(session_cookie)] = None,
) -> Response:
    # Revoking this browser's session must not require a current
    # policy. In particular, revoked/expired sessions can still be signed out.
    settings = get_settings()
    if not settings.base_url:
        raise HTTPException(503, "User origin is not configured")
    if not settings.allows_request_origin(request.headers.get("origin")):
        raise HTTPException(403, "Invalid request origin")
    token = request.cookies.get(oidc.cookie_name(settings, "session"), "")
    if TOKEN.fullmatch(token):
        try:
            redis = get_redis()
            session_key = key("session", token)
            raw = cast(bytes | None, redis.get(session_key))
            if raw is not None:
                # Validate CSRF against the existing record even when the
                # expiry, or configured access policy no longer permits access.
                try:
                    expected = json.loads(raw)["csrf_token"]
                except (ValueError, TypeError, KeyError) as exc:
                    raise HTTPException(401, "Invalid user session") from exc
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
    logger.info("user_signed_out")
    return response


oidc_logout.add_routes(
    router,
    service="user",
    get_settings=lambda: get_settings(),
    get_redis=lambda: get_redis(),
    local_logout=logout,
    cookie=cookie,
)
