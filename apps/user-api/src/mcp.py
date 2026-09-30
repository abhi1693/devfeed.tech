"""Browser consent and account-owned OAuth grants for the personal MCP resource."""

import hashlib
import json
import re
import secrets
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from devfeed_core.models import UserAccount
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError
from sqlalchemy import select

from devfeed_user_api import oidc
from devfeed_user_api.auth import UserIdentity, require_user
from devfeed_user_api.config import get_settings
from devfeed_user_api.dependencies import get_redis

router = APIRouter(prefix="/v1/user/mcp", tags=["mcp"])
READ = "devfeed:read"
WRITE = "devfeed:write"
TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def key(kind: str, value: str) -> str:
    return f"devfeed:mcp:{kind}:{hashlib.sha256(value.encode()).hexdigest()}"


def record(kind: str, value: str):
    try:
        raw = get_redis().get(key(kind, value))
        return json.loads(raw) if raw else None
    except RedisError as exc:
        raise HTTPException(503, "Agent connections temporarily unavailable") from exc


def browser_user(request: Request):
    if request.headers.get("authorization"):
        raise HTTPException(401, "Browser sign-in required")
    return require_user(request)


def require_agent_user(request: Request) -> UserIdentity:
    from devfeed_core.db import session_factory

    resource = get_settings().mcp_resource_url
    header = request.headers.get("authorization", "")
    token = header[7:] if header.startswith("Bearer ") else ""
    access = record("access", token) if resource and TOKEN.fullmatch(token) else None
    grant = record("grant", access["grant_id"]) if access else None
    if (
        not resource
        or not access
        or not grant
        or access["expires_at"] <= time.time()
        or grant["expires_at"] <= time.time()
        or access["resource"] != resource
        or grant["resource"] != resource
        or grant.get("policy") != oidc.policy_key(get_settings())
    ):
        raise HTTPException(
            401, "Agent authorization expired or revoked", headers={"WWW-Authenticate": "Bearer"}
        )
    path = request.url.path
    readable = path in {
        "/v1/user/feed",
        "/v1/user/bookmarks",
        "/v1/user/preferences",
        "/v1/user/preferences/sources",
    }
    writable = bool(
        re.fullmatch(
            r"/v1/user/(articles/[0-9a-f-]{36}/(bookmark|like)|preferences/(topics|sources)/[0-9a-f-]{36})",
            path,
        )
    )
    needed = (
        READ
        if request.method == "GET" and readable
        else WRITE
        if request.method == "PUT" and writable
        else None
    )
    if needed is None or needed not in access["scopes"]:
        raise HTTPException(403, "Agent permission denied")
    identity = grant["identity"]
    with session_factory()() as session:
        account = session.scalar(
            select(UserAccount.id).where(
                UserAccount.id == uuid.UUID(identity["user_id"]),
                UserAccount.issuer == identity["issuer"],
                UserAccount.subject == identity["subject"],
                UserAccount.organization_id == identity["organization_id"],
            )
        )
    if account is None:
        raise HTTPException(401, "User account unavailable")
    return UserIdentity(
        **identity, name=None, email=None, expires_at=access["expires_at"], csrf_token=""
    )


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool


class RequestDetails(BaseModel):
    client_name: str
    scopes: list[str]
    resource: str
    redirect_uri: str


class AuthorizationRedirect(BaseModel):
    redirect_url: str


class Connection(BaseModel):
    id: str
    client_name: str
    scopes: list[str]
    expires_at: int
    absolute_expires_at: int


class ConnectionsPage(BaseModel):
    items: list[Connection]


@router.get("/requests/{request_id}", response_model=RequestDetails)
def pending(request_id: str, request: Request):
    browser_user(request)
    value = record("pending", request_id) if TOKEN.fullmatch(request_id) else None
    if not value:
        raise HTTPException(404, "Authorization request expired")
    return {name: value[name] for name in ("client_name", "scopes", "resource", "redirect_uri")}


@router.post("/requests/{request_id}", response_model=AuthorizationRedirect)
def approve(request_id: str, payload: Consent, request: Request):
    user = browser_user(request)
    if not TOKEN.fullmatch(request_id):
        raise HTTPException(404, "Authorization request expired")
    redis = get_redis()
    raw = redis.getdel(key("pending", request_id))
    if not raw:
        raise HTTPException(404, "Authorization request expired")
    value = json.loads(raw)
    resource = get_settings().mcp_resource_url
    if not resource or value["resource"] != resource:
        raise HTTPException(400, "Unknown MCP resource")
    params = [("iss", get_settings().mcp_issuer_url or "")]
    if value.get("state"):
        params.append(("state", value["state"]))
    if not payload.approved:
        params.append(("error", "access_denied"))
    else:
        grant_id, code = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        identity = {
            name: getattr(user, name)
            for name in ("user_id", "issuer", "subject", "organization_id")
        }
        settings = get_settings()
        now = int(time.time())
        absolute_expires_at = now + settings.mcp_session_absolute_ttl_seconds
        expires_at = min(now + settings.mcp_session_ttl_seconds, absolute_expires_at)
        grant = dict(
            identity=identity,
            policy=oidc.policy_key(get_settings()),
            client_id=value["client_id"],
            client_name=value["client_name"],
            scopes=value["scopes"],
            resource=resource,
            expires_at=expires_at,
            absolute_expires_at=absolute_expires_at,
            idle_ttl_seconds=settings.mcp_session_ttl_seconds,
        )
        code_record = {
            **value,
            "grant_id": grant_id,
            "subject": user.user_id,
            "expires_at": int(time.time()) + 300,
        }
        # A bounded index lists only this account's active connections; secrets are hashed keys.
        index = key("connections", user.user_id)
        created = redis.eval(
            """
            redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
            if redis.call('ZCARD', KEYS[1]) >= 20 then return 0 end
            redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[6])
            redis.call('SET', KEYS[3], ARGV[3], 'EX', 300)
            redis.call('ZADD', KEYS[1], ARGV[4], ARGV[5])
            redis.call('EXPIRE', KEYS[1], ARGV[7])
            return 1
            """,
            3,
            index,
            key("grant", grant_id),
            key("code", code),
            int(time.time()),
            json.dumps(grant),
            json.dumps(code_record),
            expires_at,
            grant_id,
            expires_at - now,
            settings.mcp_session_absolute_ttl_seconds,
        )
        if not created:
            raise HTTPException(409, "Disconnect an agent before connecting another")
        params.append(("code", code))
    parts = urlsplit(value["redirect_uri"])
    location = urlunsplit(parts._replace(query=urlencode(parse_qsl(parts.query) + params)))
    return {"redirect_url": location}


@router.get("/connections", response_model=ConnectionsPage)
def connections(request: Request):
    user = browser_user(request)
    redis = get_redis()
    index = key("connections", user.user_id)
    redis.zremrangebyscore(index, "-inf", int(time.time()))
    items = []
    for item in redis.zrange(index, 0, 19):
        grant_id = item.decode() if isinstance(item, bytes) else item
        grant = record("grant", grant_id)
        if grant and grant["identity"]["user_id"] == user.user_id:
            items.append(
                dict(
                    id=grant_id,
                    client_name=grant["client_name"],
                    scopes=grant["scopes"],
                    expires_at=grant["expires_at"],
                    absolute_expires_at=grant.get("absolute_expires_at", grant["expires_at"]),
                )
            )
        else:
            redis.zrem(index, grant_id)
    return {"items": items}


@router.delete("/connections/{grant_id}", status_code=204, response_class=Response)
def disconnect(grant_id: str, request: Request):
    user = browser_user(request)
    grant = record("grant", grant_id) if TOKEN.fullmatch(grant_id) else None
    if grant and grant["identity"]["user_id"] != user.user_id:
        raise HTTPException(404, "Agent connection not found")
    get_redis().delete(key("grant", grant_id))
    get_redis().zrem(key("connections", user.user_id), grant_id)
