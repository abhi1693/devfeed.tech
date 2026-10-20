"""Provider logout validation and constant-time, expiring revocation lookups.

No browser tokens, ID tokens or profile fields are exposed by this module. Markers
outlive the maximum browser session plus an in-flight callback, so a concurrent
callback or renewal cannot restore access after a logout has been acknowledged.
"""

import hashlib
import json
import time
from urllib.parse import parse_qsl

import jwt
from fastapi import HTTPException, Request
from pydantic import BaseModel

from devfeed_http import oidc
from devfeed_http.body_limits import BodyLimitedRoute


class SSOLogoutResponse(BaseModel):
    url: str


# OIDC Back-Channel Logout 1.0 section 2.4 mandates this namespace verbatim.
# It identifies a signed event; it is never fetched over HTTP.
EVENT = "http://schemas.openid.net/event/backchannel-logout"  # NOSONAR: OIDC event ID.
MAX_BODY = 16_384
MAX_AGE = 900
SKEW = 60
# The reader configuration caps absolute browser sessions at 90 days. Admin and
# partner limits are shorter. Never shorten retention on a configuration change.
RETENTION = 90 * 86400 + oidc.FLOW_TTL + 2 * SKEW

RECORD_LOGOUT = """
local previous = redis.call('GET', KEYS[2])
if previous then
    if previous == ARGV[2] then return 1 else return -1 end
end
local cutoff = tonumber(redis.call('GET', KEYS[1]) or '0')
if tonumber(ARGV[1]) > cutoff then
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[4])
return 1
"""


def opaque(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 512
        and bool(value.strip())
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def scope(service: str, issuer: str, client: str) -> str:
    digest = hashlib.sha256(json.dumps([issuer, client]).encode()).hexdigest()
    return f"devfeed:{service}:provider-logout:{digest}:"


def marker(prefix: str, sid: str | None, subject: str | None) -> str:
    digest = hashlib.sha256(json.dumps([sid, subject]).encode()).hexdigest()
    return prefix + "revoked:" + digest


def correlated(claims: dict, client: str) -> dict:
    if not opaque(claims.get("sub")) or ("sid" in claims and not opaque(claims["sid"])):
        raise oidc.OIDCError("Invalid provider session claims")
    return {
        "issuer": claims["iss"],
        "client_id": client,
        "subject": claims["sub"],
        "sid": claims.get("sid"),
        "issued_at": claims["iat"],
    }


def active(redis, service: str, settings: oidc.ProviderSettings, record: dict) -> bool:
    context = record.get("provider_session")
    # Older browser sessions cannot be safely correlated. Policy versions are
    # bumped on rollout; require a new provider-validated login for those records.
    if not isinstance(context, dict):
        return False
    if (
        context.get("issuer") != settings.oidc_issuer_url
        or context.get("client_id") != settings.oidc_client_id
        or context.get("subject") != record.get("subject")
        or type(context.get("issued_at")) is not int
        or not opaque(context.get("subject"))
        or (context.get("sid") is not None and not opaque(context["sid"]))
    ):
        return False
    prefix = scope(service, context["issuer"], context["client_id"])
    subject, sid = context["subject"], context.get("sid")
    # No scan, set, or per-user list grows with session count: at most three
    # fixed-size hashed lookups. SID events never fall back to subject-wide logout.
    cutoff = redis.get(marker(prefix, None, subject))
    if cutoff is not None and context["issued_at"] <= int(cutoff):
        return False
    if sid is not None:
        for key in (marker(prefix, sid, None), marker(prefix, sid, subject)):
            if redis.get(key) is not None:
                return False
    return True


def save(redis, service: str, settings: oidc.ProviderSettings, key: str, record: dict, ttl: int):
    redis.set(key, json.dumps(record), ex=ttl)
    # A marker written before or during SET is observed here; one written after
    # this check is observed on every authenticated request, including idle renewal.
    if not active(redis, service, settings, record):
        redis.delete(key)
        raise oidc.OIDCError("Provider session has ended")


async def read_token(request: Request) -> str:
    if request.query_params or request.headers.get("content-type", "").split(";", 1)[0].lower() != (
        "application/x-www-form-urlencoded"
    ):
        raise HTTPException(400, "Invalid logout request")
    body = await request.body()  # BoundedRequestBody enforces bytes and deadline first.
    if len(body) > MAX_BODY:
        raise HTTPException(413, "Logout request too large")
    try:
        fields = parse_qsl(body.decode("ascii"), keep_blank_values=True, max_num_fields=32)
        tokens = [value for name, value in fields if name == "logout_token"]
        if len(tokens) != 1 or not tokens[0] or len(tokens[0]) > MAX_BODY:
            raise ValueError
        return tokens[0]
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, "Invalid logout request") from exc


def validate(settings: oidc.ProviderSettings, metadata: dict, token: str) -> dict:
    try:
        # Reject duplicate JSON members instead of silently choosing one identity.
        def unique(pairs):
            result = {}
            for name, value in pairs:
                if name in result:
                    raise ValueError("Duplicate JWT member")
                result[name] = value
            return result

        from base64 import urlsafe_b64decode

        parts = token.split(".")
        if len(parts) != 3 or len(token) > MAX_BODY:
            raise ValueError
        # Decode once to reject ambiguous JSON and select a configured signing key.
        # Neither the header nor the payload is trusted until jwt.decode verifies
        # the signature, issuer and audience below.
        decoded = [
            json.loads(urlsafe_b64decode(part + "=" * (-len(part) % 4)), object_pairs_hook=unique)
            for part in parts[:2]
        ]
        header = decoded[0]
        if not isinstance(header, dict) or not isinstance(decoded[1], dict):
            raise ValueError
        algorithm = header.get("alg")
        if (
            algorithm not in oidc.ALGORITHMS
            or header.get("typ", "JWT") not in {"JWT", "logout+jwt"}
            or any(name in header for name in ("jku", "x5u", "jwk", "crit"))
            or ("kid" in header and not opaque(header["kid"]))
        ):
            raise ValueError
        keys = jwt.PyJWKSet.from_dict(oidc.request_json("GET", metadata["jwks_uri"])).keys
        keys = [
            key
            for key in keys
            if key.public_key_use in {None, "sig"}
            and key.algorithm_name == algorithm
            and ("kid" not in header or key.key_id == header["kid"])
        ]
        if len(keys) != 1:
            raise ValueError
        claims = jwt.decode(
            token,
            keys[0],
            algorithms=[algorithm],
            issuer=settings.oidc_issuer_url,
            audience=settings.oidc_client_id,
            leeway=SKEW,
            options={
                "verify_signature": True,
                "require": ["iss", "aud", "iat", "jti", "events"],
            },
        )
        now = int(time.time())
        audience = claims["aud"]
        if (
            type(claims["iat"]) is not int
            or not now - MAX_AGE <= claims["iat"] <= now + SKEW
            or ("exp" in claims and (type(claims["exp"]) is not int or claims["exp"] <= now))
            or not opaque(claims["jti"])
            or claims["events"] != {EVENT: {}}
            or "nonce" in claims
            or not ("sid" in claims or "sub" in claims)
            or any(name in claims and not opaque(claims[name]) for name in ("sid", "sub"))
            or not (
                isinstance(audience, str)
                and opaque(audience)
                or isinstance(audience, list)
                and 0 < len(audience) <= 16
                and all(opaque(value) for value in audience)
                and len(set(audience)) == len(audience)
            )
        ):
            raise ValueError
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        raise oidc.OIDCError("Invalid logout token") from exc


def revoke(redis, service: str, settings: oidc.ProviderSettings, claims: dict) -> None:
    prefix = scope(service, str(settings.oidc_issuer_url), str(settings.oidc_client_id))
    target = marker(prefix, claims.get("sid"), claims.get("sub"))
    fingerprint = hashlib.sha256(json.dumps(claims, sort_keys=True).encode()).hexdigest()
    replay = prefix + "event:" + hashlib.sha256(claims["jti"].encode()).hexdigest()
    # One atomic write records revocation before acknowledging the delivery. An
    # out-of-order subject event cannot reduce the cutoff; exact retries succeed.
    result = redis.eval(
        RECORD_LOGOUT,
        2,
        target,
        replay,
        claims["iat"],
        fingerprint,
        RETENTION,
        MAX_AGE + 2 * SKEW,
    )
    if result != 1:
        raise oidc.OIDCError("Conflicting logout delivery")


def add_routes(router, *, service, get_settings, get_redis, local_logout, cookie):
    """Register independently authenticated provider requests and browser SSO flow."""
    import hmac
    import secrets
    from urllib.parse import urlencode

    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import JSONResponse, RedirectResponse, Response
    from redis.exceptions import RedisError

    from devfeed_http.operation_policies import (
        ExtensionTransportPolicy,
        GatewayPolicy,
        ReaderGatewayPolicy,
    )

    def origin(settings):
        value = settings.base_url if service == "user" else getattr(settings, f"{service}_base_url")
        return str(value).rstrip("/") if value else None

    def flow_key(settings, state):
        prefix = scope(service, str(settings.oidc_issuer_url), str(settings.oidc_client_id))
        return prefix + "flow:" + hashlib.sha256(state.encode()).hexdigest()

    def browser_policy(settings):
        # Bind completion to the configured client, origin and current service policy.
        return [
            settings.oidc_issuer_url,
            settings.oidc_client_id,
            origin(settings),
            settings.allowed_origins if service == "user" else [],
        ]

    def process(token):
        settings = get_settings()
        if not oidc.configured(settings, base_url=origin(settings)):
            raise HTTPException(503, "Provider logout is not configured")
        try:
            metadata = oidc.discovery(settings)
        except oidc.OIDCError as exc:
            raise HTTPException(503, "Provider logout temporarily unavailable") from exc
        try:
            claims = validate(settings, metadata, token)
            revoke(get_redis(), service, settings, claims)
        except oidc.OIDCDependencyError as exc:
            raise HTTPException(503, "Provider logout temporarily unavailable") from exc
        except RedisError as exc:
            raise HTTPException(503, "Could not revoke sessions; retry delivery") from exc
        except oidc.OIDCError as exc:
            raise HTTPException(400, "Invalid logout token") from exc

    provider_policy = (
        ReaderGatewayPolicy(
            authentication="logout-token", body_limit=MAX_BODY, cookie_policy="omit"
        )
        if service == "user"
        else GatewayPolicy(authentication="logout-token", body_limit=MAX_BODY)
    )

    async def backchannel_logout(request: Request):
        token = await read_token(request)
        await run_in_threadpool(process, token)
        return Response(status_code=200, headers={"Cache-Control": "no-store"})

    router.add_api_route(
        "/backchannel-logout",
        backchannel_logout,
        methods=["POST"],
        operation_id=f"{service}_auth_backchannel_logout",
        status_code=200,
        response_class=Response,
        response_model=None,
        route_class_override=BodyLimitedRoute,
        openapi_extra=provider_policy.bounded_openapi_extra(),
    )

    @router.post(
        "/sso/logout",
        operation_id=f"{service}_auth_sso_logout",
        response_model=SSOLogoutResponse,
        openapi_extra=ReaderGatewayPolicy(extension=ExtensionTransportPolicy()).openapi_extra()
        if service == "user"
        else None,
    )
    def sso_logout(request: Request):
        settings = get_settings()
        if not oidc.configured(settings, base_url=origin(settings)):
            raise HTTPException(503, "SSO sign-out is not configured")
        try:
            metadata = oidc.discovery(settings)
            endpoint = oidc.endpoint(metadata.get("end_session_endpoint"), settings)
            # Restrict logout to the configured issuer origin, not an arbitrary
            # discovery URL. No token-supplied redirect or identity hint is used.
            from urllib.parse import urlsplit

            if urlsplit(endpoint).netloc != urlsplit(str(settings.oidc_issuer_url)).netloc:
                raise oidc.OIDCError("Invalid end-session endpoint")
            state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            # Reader aliases have host-only cookies of their own. Return to the
            # same explicitly trusted origin; its post-logout URI must also be registered.
            completion_origin = origin(settings)
            if service == "user" and request.headers.get("origin") in settings.allowed_origins:
                completion_origin = request.headers["origin"]
            flow = {
                "browser": browser,
                "policy": browser_policy(settings),
                "completion_origin": completion_origin,
            }
            get_redis().set(flow_key(settings, state), json.dumps(flow), ex=oidc.FLOW_TTL)
            # Reuse the local revocation primitive and its origin/CSRF checks
            # before opening the provider as part of the single sign-out action.
            local = local_logout(request)
            query = urlencode(
                {
                    "client_id": settings.oidc_client_id,
                    "post_logout_redirect_uri": completion_origin
                    + f"/api/v1/{service}/auth/sso/callback",
                    "state": state,
                }
            )
            response = JSONResponse({"url": endpoint + ("&" if "?" in endpoint else "?") + query})
            response.headers["Cache-Control"] = "no-store"
            for name, value in local.raw_headers:
                if name == b"set-cookie":
                    response.raw_headers.append((name, value))
            cookie(response, "logout_state", browser, oidc.FLOW_TTL)
            return response
        except (oidc.OIDCError, RedisError) as exc:
            raise HTTPException(503, "SSO sign-out temporarily unavailable") from exc

    @router.get(
        "/sso/callback",
        operation_id=f"{service}_auth_sso_callback",
        status_code=302,
        response_class=RedirectResponse,
    )
    def sso_callback(request: Request):
        settings = get_settings()
        states = request.query_params.getlist("state")
        browser = request.cookies.get(
            oidc.cookie_name(
                service,
                "logout_state",
                secure=(
                    settings.cookie_secure
                    if service == "user"
                    else getattr(settings, f"{service}_cookie_secure")
                ),
            ),
            "",
        )
        if (
            len(states) != 1
            or not oidc.TOKEN.fullmatch(states[0])
            or not oidc.TOKEN.fullmatch(browser)
        ):
            raise HTTPException(400, "Invalid SSO logout completion")
        try:
            key = flow_key(settings, states[0])
            redis = get_redis()
            raw = redis.get(key)
            flow = json.loads(raw) if raw else {}
            if (
                flow.get("policy") != browser_policy(settings)
                or flow.get("completion_origin")
                not in [origin(settings), *(settings.allowed_origins if service == "user" else [])]
                or not isinstance(flow.get("browser"), str)
                or not hmac.compare_digest(flow["browser"], browser)
                or redis.getdel(key) != raw
            ):
                raise ValueError
        except RedisError as exc:
            raise HTTPException(503, "SSO logout completion temporarily unavailable") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, "Invalid SSO logout completion") from exc
        response = RedirectResponse(flow["completion_origin"] + "/login?logout=sso", 302)
        response.headers["Cache-Control"] = "no-store"
        cookie(response, "logout_state", "", 0)
        return response
