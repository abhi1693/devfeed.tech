"""Admin identity and revocable Redis sessions; no user accounts or passwords."""

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

from devfeed_admin_api import oidc
from devfeed_admin_api.config import get_settings
from devfeed_admin_api.dependencies import get_redis

router = APIRouter(prefix="/v1/admin/auth", tags=["admin-auth"])
logger = logging.getLogger(__name__)
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
session_cookie = APIKeyCookie(
    name="__Host-devfeed_admin_session",
    auto_error=False,
    description="Admin session cookie (devfeed_admin_session in HTTP development).",
)


class AuthConfig(BaseModel):
    enabled: bool


class AdminIdentity(BaseModel):
    subject: str
    issuer: str
    organization_id: str
    roles: list[str]
    name: str | None = None
    email: str | None = None
    expires_at: int
    csrf_token: str


def key(kind: str, token: str) -> str:
    return f"devfeed:admin:{kind}:{hashlib.sha256(token.encode()).hexdigest()}"


def require_config() -> None:
    if not oidc.configured(get_settings()):
        raise HTTPException(503, "Admin authentication is not configured")


def cookie(response: Response, kind: str, value: str, ttl: int) -> None:
    settings = get_settings()
    response.set_cookie(
        oidc.cookie_name(settings, kind),
        value,
        max_age=ttl,
        httponly=True,
        secure=settings.admin_cookie_secure,
        samesite="lax",
        path="/",
    )


def require_admin(
    request: Request,
    _cookie: Annotated[str | None, Security(session_cookie)] = None,
) -> AdminIdentity:
    require_config()
    settings = get_settings()
    token = request.cookies.get(oidc.cookie_name(settings, "session"), "")
    if not TOKEN.fullmatch(token):
        raise HTTPException(401, "Admin sign-in required")
    try:
        raw = cast(bytes | None, get_redis().get(key("session", token)))
    except RedisError as exc:
        raise HTTPException(503, "Admin sessions temporarily unavailable") from exc
    if raw is None:
        raise HTTPException(401, "Admin session expired")
    try:
        record = json.loads(raw)
        admin = AdminIdentity.model_validate(record)
        if admin.expires_at <= time.time() or record.get("policy") != oidc.policy_key(settings):
            raise ValueError("Expired or invalidated session")
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(401, "Admin session expired") from exc
    if settings.admin_required_role not in admin.roles:
        raise HTTPException(403, "Required admin role is not granted")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("x-csrf-token", "")
        if (
            request.headers.get("origin") != str(settings.admin_base_url).rstrip("/")
            or not TOKEN.fullmatch(supplied)
            or not hmac.compare_digest(supplied, admin.csrf_token)
        ):
            raise HTTPException(403, "Invalid request origin or CSRF token")
    return admin


Admin = Annotated[AdminIdentity, Depends(require_admin)]


def actor(admin: AdminIdentity) -> dict[str, str]:
    identity = {key: getattr(admin, key) for key in ("subject", "issuer", "organization_id")}
    for field in ("name", "email"):
        value = getattr(admin, field)
        if value and value.strip():
            identity[field] = value.strip()
    return identity


@router.get("/config", response_model=AuthConfig, operation_id="admin_auth_config")
def config():
    return AuthConfig(enabled=oidc.configured(get_settings()))


@router.get("/login", operation_id="admin_auth_login", response_class=RedirectResponse)
def login(request: Request, reauthenticate: bool = False):
    require_config()
    settings = get_settings()
    try:
        metadata = oidc.discovery(settings)
        location, flow = oidc.start(settings, metadata, reauthenticate=reauthenticate)
        get_redis().set(key("flow", flow["state"]), json.dumps(flow), ex=oidc.FLOW_TTL)
    except (oidc.OIDCError, RedisError) as exc:
        logger.warning("admin_login_unavailable", extra={"error_type": type(exc).__name__})
        raise HTTPException(503, "Admin sign-in temporarily unavailable") from exc
    response = RedirectResponse(location, status_code=302)
    # Each browser can have one outstanding flow. A subsequent login supersedes it.
    cookie(response, "state", flow["browser"], oidc.FLOW_TTL)
    return response


@router.get("/callback", operation_id="admin_auth_callback", response_class=RedirectResponse)
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
        # even if the new identity fails authorization. Never do this for an
        # unbound/replayed callback: that would allow forced logout by URL.
        previous = request.cookies.get(oidc.cookie_name(settings, "session"), "")
        if TOKEN.fullmatch(previous):
            redis.delete(key("session", previous))
        if params.get("error") or not params.get("code") or len(params["code"]) > 4096:
            raise oidc.OIDCError("Authorization was not completed")
        if "iss" in params and params["iss"] != settings.oidc_issuer_url:
            raise oidc.OIDCError("Authorization issuer mismatch")
        admin = oidc.identity(settings, oidc.discovery(settings), flow, params["code"])
        if settings.admin_required_role not in admin["roles"]:
            failure = "access_denied"
            raise oidc.OIDCError("Required admin role is not granted")
        ttl = admin["expires_at"] - int(time.time())
        if ttl <= 0:
            raise oidc.OIDCError("Expired identity")
        token = secrets.token_urlsafe(32)
        redis.set(key("session", token), json.dumps(admin), ex=ttl)
        response = RedirectResponse(str(settings.admin_base_url).rstrip("/") + "/start", 302)
        cookie(response, "session", token, ttl)
        logger.info("admin_signed_in")
    except (oidc.OIDCError, RedisError, ValueError, KeyError, TypeError) as exc:
        logger.warning("admin_login_failed", extra={"error_type": type(exc).__name__})
        response = RedirectResponse(
            str(settings.admin_base_url).rstrip("/") + f"/login?error={failure}", 302
        )
        if bound_flow:
            cookie(response, "session", "", 0)
    cookie(response, "state", "", 0)
    return response


@router.get("/me", response_model=AdminIdentity, operation_id="admin_auth_me")
def me(admin: Admin):
    return admin


@router.post("/logout", status_code=204, operation_id="admin_auth_logout")
def logout(
    request: Request,
    _cookie: Annotated[str | None, Security(session_cookie)] = None,
):
    # Revoking this browser's session must not require a current admin grant or
    # policy. In particular, revoked/expired sessions can still be signed out.
    settings = get_settings()
    if not settings.admin_base_url:
        raise HTTPException(503, "Admin origin is not configured")
    if request.headers.get("origin") != str(settings.admin_base_url).rstrip("/"):
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
                    raise HTTPException(401, "Invalid admin session") from exc
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
    logger.info("admin_signed_out")
    return response
