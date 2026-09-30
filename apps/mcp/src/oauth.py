"""OAuth SDK provider backed by the user service's revocable Redis grants."""

import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from redis.asyncio import Redis

RESOURCE_SCOPES = ["devfeed:read", "devfeed:write"]
SCOPES = [*RESOURCE_SCOPES, "offline_access"]


def key(kind: str, value: str) -> str:
    return f"devfeed:mcp:{kind}:{hashlib.sha256(value.encode()).hexdigest()}"


class OAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, redis: Redis, resource: str, web_url: str, access_ttl_seconds: int = 900):
        self.access_ttl_seconds = access_ttl_seconds
        self.redis = redis
        self.resource = resource
        self.web_url = web_url.rstrip("/")

    async def get_record(self, kind: str, value: str):
        raw = await self.redis.get(key(kind, value))
        return json.loads(raw) if raw else None

    async def put_record(self, kind: str, value: str, record: dict, ttl: int):
        await self.redis.set(key(kind, value), json.dumps(record), ex=ttl)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        value = await self.get_record("client", client_id)
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Bound public registration storage without trusting forwarded client addresses.
        bucket = f"devfeed:mcp:registrations:{int(time.time()) // 60}"
        count = await self.redis.incr(bucket)
        await self.redis.expire(bucket, 120)
        if count > 100:
            raise RegistrationError("invalid_client_metadata", "Please retry later.")
        if client_info.token_endpoint_auth_method != "none":
            raise RegistrationError("invalid_client_metadata", "Use public clients with PKCE.")
        if not client_info.redirect_uris or not 1 <= len(client_info.redirect_uris) <= 10:
            raise RegistrationError("invalid_redirect_uri")
        for uri in client_info.redirect_uris:
            parts = urlsplit(str(uri))
            if (
                not parts.hostname
                or parts.username
                or parts.password
                or parts.fragment
                or len(str(uri)) > 2000
                or (
                    parts.scheme != "https"
                    and not (
                        parts.scheme == "http"
                        and parts.hostname in {"localhost", "127.0.0.1", "::1"}
                    )
                )
            ):
                raise RegistrationError("invalid_redirect_uri")
        await self.put_record(
            "client", client_info.client_id, client_info.model_dump(mode="json"), 90 * 86400
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        bucket = f"devfeed:mcp:authorizations:{client.client_id}:{int(time.time()) // 60}"
        count = await self.redis.incr(bucket)
        await self.redis.expire(bucket, 120)
        if count > 20:
            raise AuthorizeError("temporarily_unavailable", "Please retry later.")
        if params.resource not in {None, self.resource}:
            raise AuthorizeError("invalid_target")
        scopes = params.scopes or SCOPES
        if SCOPES[0] not in scopes or set(scopes) - set(SCOPES):
            raise AuthorizeError("invalid_scope")
        request_id = secrets.token_urlsafe(32)
        record = params.model_dump(mode="json")
        record.update(
            client_id=client.client_id,
            client_name=client.client_name or "MCP client",
            scopes=scopes,
            resource=self.resource,
        )
        await self.put_record("pending", request_id, record, 600)
        return f"{self.web_url}/mcp/authorize?{urlencode({'request': request_id})}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ):
        record = await self.get_record("code", authorization_code)
        if not record or record["client_id"] != client.client_id:
            return None
        return AuthorizationCode.model_validate({**record, "code": authorization_code})

    async def issue(self, grant_id: str, client_id: str, scopes: list[str]) -> OAuthToken:
        grant = await self.get_record("grant", grant_id)
        if not grant or grant["client_id"] != client_id or grant["expires_at"] <= time.time():
            raise TokenError("invalid_grant")
        if grant["resource"] != self.resource or set(scopes) - set(grant["scopes"]):
            raise TokenError("invalid_scope")
        now = int(time.time())
        renewed = await self.redis.eval(
            """
            local raw = redis.call('GET', KEYS[1])
            if not raw then return false end
            local grant = cjson.decode(raw)
            local absolute = grant.absolute_expires_at or grant.expires_at
            if grant.expires_at <= tonumber(ARGV[1]) or absolute <= tonumber(ARGV[1]) then
                return false
            end
            local idle = grant.idle_ttl_seconds or (absolute - tonumber(ARGV[1]))
            grant.expires_at = math.min(tonumber(ARGV[1]) + idle, absolute)
            local updated = cjson.encode(grant)
            redis.call('SET', KEYS[1], updated, 'EX', grant.expires_at - tonumber(ARGV[1]))
            redis.call('ZADD', KEYS[2], grant.expires_at, ARGV[2])
            local keep = math.max(redis.call('TTL', KEYS[2]), absolute - tonumber(ARGV[1]))
            redis.call('EXPIRE', KEYS[2], keep)
            return updated
            """,
            2,
            key("grant", grant_id),
            key("connections", grant["identity"]["user_id"]),
            now,
            grant_id,
        )
        if not renewed:
            raise TokenError("invalid_grant")
        grant = json.loads(renewed)
        ttl = min(self.access_ttl_seconds, int(grant["expires_at"] - now))
        # Active clients must not lose registration before their grant expires.
        await self.redis.expire(
            key("client", client_id),
            max(90 * 86400, grant.get("absolute_expires_at", grant["expires_at"]) - now),
        )
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        record = dict(
            client_id=client_id,
            scopes=scopes,
            grant_id=grant_id,
            resource=self.resource,
            subject=grant["identity"]["user_id"],
        )
        await self.put_record(
            "access", access, {**record, "expires_at": int(time.time()) + ttl}, ttl
        )
        await self.put_record(
            "refresh",
            refresh,
            {**record, "expires_at": grant["expires_at"]},
            int(grant["expires_at"] - time.time()),
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ttl,
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ):
        raw = await self.redis.getdel(key("code", authorization_code.code))
        if not raw:
            raise TokenError("invalid_grant")
        record = json.loads(raw)
        return await self.issue(record["grant_id"], client.client_id, authorization_code.scopes)

    async def revoke_grant(self, grant_id: str):
        grant = await self.get_record("grant", grant_id)
        await self.redis.delete(key("grant", grant_id))
        if grant:
            await self.redis.zrem(key("connections", grant["identity"]["user_id"]), grant_id)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        record = await self.get_record("refresh", refresh_token)
        if not record:
            replay = await self.get_record("used-refresh", refresh_token)
            if replay and replay["client_id"] == client.client_id:
                await self.revoke_grant(replay["grant_id"])
            return None
        if record["client_id"] != client.client_id:
            return None
        return RefreshToken.model_validate({**record, "token": refresh_token})

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ):
        record = await self.get_record("refresh", refresh_token.token)
        if not record:
            replay = await self.get_record("used-refresh", refresh_token.token)
            if replay and replay["client_id"] == client.client_id:
                await self.revoke_grant(replay["grant_id"])
            raise TokenError("invalid_grant")
        grant = await self.get_record("grant", record["grant_id"])
        if not grant:
            raise TokenError("invalid_grant")
        ttl = max(1, int(grant.get("absolute_expires_at", grant["expires_at"]) - time.time()))
        raw = await self.redis.eval(
            """
            local raw = redis.call('GET', KEYS[1])
            if not raw then return false end
            redis.call('SET', KEYS[2], raw, 'EX', ARGV[1])
            redis.call('DEL', KEYS[1])
            return raw
            """,
            2,
            key("refresh", refresh_token.token),
            key("used-refresh", refresh_token.token),
            ttl,
        )
        if not raw:
            await self.revoke_grant(record["grant_id"])
            raise TokenError("invalid_grant")
        record = json.loads(raw)
        return await self.issue(record["grant_id"], client.client_id, scopes)

    async def load_access_token(self, token: str):
        record = await self.get_record("access", token)
        if not record or record["expires_at"] <= time.time() or record["resource"] != self.resource:
            return None
        grant = await self.get_record("grant", record["grant_id"])
        if not grant or grant["expires_at"] <= time.time():
            return None
        return AccessToken.model_validate({**record, "token": token})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        kind = "access" if isinstance(token, AccessToken) else "refresh"
        record = await self.get_record(kind, token.token)
        if record:
            await self.revoke_grant(record["grant_id"])


class OAuthPolicyMiddleware:
    """Bound OAuth bodies and enforce resource indicators on token exchanges."""

    def __init__(self, app, resource: str):
        self.app = app
        self.resource = resource

    async def __call__(self, scope, receive, send):
        from urllib.parse import parse_qs

        from starlette.responses import JSONResponse

        if scope["type"] != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 32768:
                return await JSONResponse({"error": "invalid_request"}, status_code=413)(
                    scope, receive, send
                )
            if not message.get("more_body", False):
                break
        if scope["path"] == "/token":
            try:
                resources = parse_qs(body.decode()).get("resource", [])
            except UnicodeError:
                resources = ["invalid"]
            if resources and resources != [self.resource]:
                return await JSONResponse(
                    {"error": "invalid_target"},
                    status_code=400,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
