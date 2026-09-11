"""Optional user identity and isolated revocable browser sessions."""

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyCookie
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from devfeed_user_api import oidc
from devfeed_user_api.accounts import save_user
from devfeed_user_api.config import get_settings
from devfeed_user_api.dependencies import get_redis

router = APIRouter(prefix="/v1/user/auth", tags=["user-auth"])
logger = logging.getLogger(__name__)
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
session_cookie = APIKeyCookie(
    name="__Host-devfeed_user_session",
    auto_error=False,
    description="User session cookie (devfeed_user_session in HTTP development).",
)


class AuthConfig(BaseModel):
    enabled: bool


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
        user = UserIdentity.model_validate(record)
        if user.expires_at <= time.time() or record.get("policy") != oidc.policy_key(settings):
            raise ValueError("Expired or invalidated session")
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(401, "User session expired") from exc
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("x-csrf-token", "")
        if (
            request.headers.get("origin") != str(settings.base_url).rstrip("/")
            or not TOKEN.fullmatch(supplied)
            or not hmac.compare_digest(supplied, user.csrf_token)
        ):
            raise HTTPException(403, "Invalid request origin or CSRF token")
    return user


User = Annotated[UserIdentity, Depends(require_user)]


@router.get("/config", response_model=AuthConfig, operation_id="user_auth_config")
def config():
    return AuthConfig(enabled=oidc.configured(get_settings()))


@router.get("/login", operation_id="user_auth_login", response_class=RedirectResponse)
def login(request: Request, register: bool = False, return_to: str = "/my-feed"):
    if not re.fullmatch(
        r"/(?:my-feed|settings/topics|articles/[a-zA-Z0-9][a-zA-Z0-9-]{0,199})", return_to
    ):
        raise HTTPException(422, "Invalid sign-in destination")
    require_config()
    settings = get_settings()
    try:
        metadata = oidc.discovery(settings)
        location, flow = oidc.start(settings, metadata, register=register)
        flow["return_to"] = return_to
        get_redis().set(key("flow", flow["state"]), json.dumps(flow), ex=oidc.FLOW_TTL)
    except (oidc.OIDCError, RedisError) as exc:
        logger.warning("user_login_unavailable", extra={"error_type": type(exc).__name__})
        raise HTTPException(503, "User sign-in temporarily unavailable") from exc
    response = RedirectResponse(location, status_code=302)
    # Each browser can have one outstanding flow. A subsequent login supersedes it.
    cookie(response, "state", flow["browser"], oidc.FLOW_TTL)
    return response


@router.get("/callback", operation_id="user_auth_callback", response_class=RedirectResponse)
def callback(request: Request):
    require_config()
    settings = get_settings()
    failure = "login_failed"
    bound_flow = False
    try:
        # Reject ambiguous repeated query parameters, including provider errors.
        params = request.query_params
        if any(len(params.getlist(name)) > 1 for name in ("state", "code", "error", "iss")):
            raise oidc.OIDCError("Ambiguous callback")
        state = params.get("state", "")
        browser = request.cookies.get(oidc.cookie_name(settings, "state"), "")
        if not TOKEN.fullmatch(state) or not TOKEN.fullmatch(browser):
            raise oidc.OIDCError("Missing login state")
        redis = get_redis()
        raw = cast(bytes | None, redis.get(key("flow", state)))
        if not raw:
            raise oidc.OIDCError("Expired login state")
        flow = json.loads(raw)
        if not hmac.compare_digest(flow["browser"], browser) or flow["policy"] != oidc.policy_key(
            settings
        ):
            raise oidc.OIDCError("Invalid login state")
        # GETDEL consumes the flow exactly once, including concurrent callbacks.
        if redis.getdel(key("flow", state)) != raw:
            raise oidc.OIDCError("Login state already used")
        bound_flow = True
        # A browser-validated sign-in replaces its previous local identity,
        # even if the new identity fails validation. Never do this for an
        # unbound/replayed callback: that would allow forced logout by URL.
        previous = request.cookies.get(oidc.cookie_name(settings, "session"), "")
        if TOKEN.fullmatch(previous):
            redis.delete(key("session", previous))
        if params.get("error") or not params.get("code") or len(params["code"]) > 4096:
            raise oidc.OIDCError("Authorization was not completed")
        if "iss" in params and params["iss"] != settings.oidc_issuer_url:
            raise oidc.OIDCError("Authorization issuer mismatch")
        user = oidc.identity(settings, oidc.discovery(settings), flow, params["code"])
        ttl = user["expires_at"] - int(time.time())
        if ttl <= 0:
            raise oidc.OIDCError("Expired identity")
        user["user_id"] = save_user(user)
        token = secrets.token_urlsafe(32)
        redis.set(key("session", token), json.dumps(user), ex=ttl)
        response = RedirectResponse(
            str(settings.base_url).rstrip("/") + flow.get("return_to", "/my-feed"), 302
        )
        cookie(response, "session", token, ttl)
        logger.info("user_signed_in")
    except (oidc.OIDCError, RedisError, SQLAlchemyError, ValueError, KeyError, TypeError) as exc:
        logger.warning("user_login_failed", extra={"error_type": type(exc).__name__})
        response = RedirectResponse(
            str(settings.base_url).rstrip("/") + f"/login?error={failure}", 302
        )
        if bound_flow:
            cookie(response, "session", "", 0)
    cookie(response, "state", "", 0)
    return response


@router.get("/me", response_model=UserIdentity | None, operation_id="user_auth_me")
def me(request: Request):
    # Anonymous browsing is normal; protected routes still use require_user.
    try:
        return require_user(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return None
        raise


@router.post("/logout", status_code=204, operation_id="user_auth_logout")
def logout(
    request: Request,
    _cookie: Annotated[str | None, Security(session_cookie)] = None,
):
    # Revoking this browser's session must not require a current
    # policy. In particular, revoked/expired sessions can still be signed out.
    settings = get_settings()
    if not settings.base_url:
        raise HTTPException(503, "User origin is not configured")
    if request.headers.get("origin") != str(settings.base_url).rstrip("/"):
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
