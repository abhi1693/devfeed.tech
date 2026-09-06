"""Generic OIDC authorization-code client. Provider tokens never reach the browser."""

import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64encode
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

from devfeed_admin_api.config import Settings
from devfeed_admin_api.roles import verified_roles

ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]
FLOW_TTL = 600


class OIDCError(Exception):
    """Deliberately contains no provider response, URL, credentials, or token."""


def configured(settings: Settings) -> bool:
    return bool(
        settings.admin_base_url
        and settings.oidc_issuer_url
        and settings.oidc_client_id
        and settings.oidc_organization_id
        and (
            settings.oidc_token_endpoint_auth_method == "none"
            or (settings.oidc_client_secret and settings.oidc_client_secret.get_secret_value())
        )
    )


def policy_key(settings: Settings) -> str:
    """Invalidate in-flight logins and sessions after issuer/client/access-policy changes."""
    values = {
        key: value
        for key, value in settings.model_dump(mode="json").items()
        if key.startswith(("oidc_", "admin_"))
    }
    if settings.oidc_client_secret:
        values["oidc_client_secret"] = settings.oidc_client_secret.get_secret_value()
    values["authorization_policy_version"] = "organization-role-v1"
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def redirect_uri(settings: Settings) -> str:
    return f"{str(settings.admin_base_url).rstrip('/')}/api/v1/admin/auth/callback"


def cookie_name(settings: Settings, kind: str) -> str:
    prefix = "__Host-" if settings.admin_cookie_secure else ""
    return f"{prefix}devfeed_admin_{kind}"


def endpoint(value: object, settings: Settings) -> str:
    if not isinstance(value, str):
        raise OIDCError("Invalid discovery endpoint")
    try:
        url = urlsplit(value)
        issuer = urlsplit(str(settings.oidc_issuer_url))
    except ValueError as exc:
        raise OIDCError("Invalid discovery endpoint") from exc
    local_http = (
        issuer.scheme == "http"
        and url.hostname == issuer.hostname
        and url.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    if (
        not url.hostname
        or url.username
        or url.password
        or url.fragment
        or (url.scheme != "https" and not (url.scheme == "http" and local_http))
    ):
        raise OIDCError("Invalid discovery endpoint")
    return value


def request_json(method: str, url: str, **kwargs) -> dict:
    # Bounded requests; never follow redirects carrying a secret or bearer token.
    try:
        with (
            httpx.Client(timeout=10, follow_redirects=False) as client,
            client.stream(method, url, headers={"Accept": "application/json"}, **kwargs) as res,
        ):
            if res.status_code != 200:
                raise OIDCError("OIDC provider request failed")
            body = bytearray()
            for chunk in res.iter_bytes():
                body.extend(chunk)
                if len(body) > 1_000_000:
                    raise OIDCError("OIDC provider response too large")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise OIDCError("Invalid OIDC provider response")
        return result
    except (httpx.HTTPError, ValueError) as exc:
        raise OIDCError("OIDC provider request failed") from exc


def discovery(settings: Settings) -> dict:
    issuer = str(settings.oidc_issuer_url)
    metadata = request_json("GET", f"{issuer.rstrip('/')}/.well-known/openid-configuration")
    if metadata.get("issuer") != issuer:
        raise OIDCError("Discovery issuer mismatch")
    for name in ("code_challenge_methods_supported", "token_endpoint_auth_methods_supported"):
        if name in metadata and (
            not isinstance(metadata[name], list)
            or not all(isinstance(item, str) for item in metadata[name])
        ):
            raise OIDCError("Invalid discovery capabilities")
    for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        endpoint(metadata.get(name), settings)
    if "userinfo_endpoint" in metadata:
        endpoint(metadata["userinfo_endpoint"], settings)
    # Some providers implement PKCE without advertising it. Always send S256;
    # explicitly incompatible metadata is rejected, never downgraded to plain.
    if (
        "code_challenge_methods_supported" in metadata
        and "S256" not in metadata["code_challenge_methods_supported"]
    ):
        raise OIDCError("Provider does not support PKCE S256")
    if settings.oidc_token_endpoint_auth_method not in metadata.get(
        "token_endpoint_auth_methods_supported", ["client_secret_basic"]
    ):
        raise OIDCError("Provider does not support configured client authentication")
    return metadata


def start(settings: Settings, metadata: dict, *, reauthenticate: bool = False) -> tuple[str, dict]:
    state, nonce, verifier, browser = (secrets.token_urlsafe(32) for _ in range(4))
    challenge = urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    scopes = list(dict.fromkeys(["openid", *settings.oidc_scopes]))
    scopes.append(
        settings.oidc_organization_scope_template.format(
            organization_id=settings.oidc_organization_id
        )
    )
    if settings.oidc_role_scope_template:
        scopes.append(settings.oidc_role_scope_template.format(role=settings.admin_required_role))
    params = {
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri(settings),
        "response_type": "code",
        "response_mode": "query",
        "scope": " ".join(dict.fromkeys(scopes)),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if reauthenticate:
        # Standard OIDC recovery from a stale/wrong provider session. max_age
        # requires an auth_time claim, which we verify against this saved flow.
        params.update(prompt="login", max_age="0")
    separator = "&" if "?" in metadata["authorization_endpoint"] else "?"
    return metadata["authorization_endpoint"] + separator + urlencode(params), {
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "browser": browser,
        "policy": policy_key(settings),
        "reauthenticate": reauthenticate,
        "started_at": int(time.time()),
    }


def identity(settings: Settings, metadata: dict, flow: dict, code: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(settings),
        "code_verifier": flow["verifier"],
    }
    auth = None
    secret = settings.oidc_client_secret.get_secret_value() if settings.oidc_client_secret else ""
    if settings.oidc_token_endpoint_auth_method == "client_secret_basic":
        auth = httpx.BasicAuth(str(settings.oidc_client_id), secret)
    else:
        data["client_id"] = str(settings.oidc_client_id)
        if settings.oidc_token_endpoint_auth_method == "client_secret_post":
            data["client_secret"] = secret
    tokens = request_json("POST", metadata["token_endpoint"], data=data, auth=auth)
    try:
        raw_token = tokens["id_token"]
        header = jwt.get_unverified_header(raw_token)
        keyset = jwt.PyJWKSet.from_dict(request_json("GET", metadata["jwks_uri"]))
        keys = [key for key in keyset.keys if key.public_key_use in {None, "sig"}]
        if "kid" in header:
            keys = [key for key in keys if key.key_id == header["kid"]]
        if len(keys) != 1 or header.get("alg") not in ALGORITHMS:
            raise OIDCError("Invalid ID token signing key")
        claims = jwt.decode(
            raw_token,
            keys[0],
            algorithms=ALGORITHMS,
            issuer=settings.oidc_issuer_url,
            audience=settings.oidc_client_id,
            leeway=60,
            options={"require": ["iss", "aud", "sub", "exp", "iat", "nonce"]},
        )
        # Bind optional OIDC hash claims to this exchange before using UserInfo.
        for claim_name, value in (("at_hash", tokens.get("access_token")), ("c_hash", code)):
            if claim_name in claims:
                if not isinstance(value, str) or not isinstance(claims[claim_name], str):
                    raise OIDCError("Invalid token hash claim")
                digest = jwt.get_algorithm_by_name(header["alg"]).compute_hash_digest(
                    value.encode()
                )
                expected_hash = urlsafe_b64encode(digest[: len(digest) // 2]).rstrip(b"=").decode()
                if not hmac.compare_digest(claims[claim_name], expected_hash):
                    raise OIDCError("Token hash mismatch")
        audience = claims["aud"]
        if (
            not isinstance(claims["nonce"], str)
            or not hmac.compare_digest(claims["nonce"], flow["nonce"])
            or ("azp" in claims and claims["azp"] != settings.oidc_client_id)
            or (isinstance(audience, list) and len(audience) > 1 and "azp" not in claims)
            or not claims["sub"]
            or type(claims["exp"]) is not int
            or type(claims["iat"]) is not int
        ):
            raise OIDCError("Invalid ID token claims")
        if flow.get("reauthenticate"):
            authenticated_at = claims.get("auth_time")
            if (
                type(authenticated_at) is not int
                or authenticated_at < flow["started_at"] - 60
                or authenticated_at > int(time.time()) + 60
            ):
                raise OIDCError("Fresh authentication was not completed")
        profile = dict(claims)
        info: dict = {}
        # Profile/organization claims may be delivered via UserInfo instead. Never
        # overwrite token security claims, and require an exact subject match.
        if metadata.get("userinfo_endpoint") and tokens.get("access_token"):
            info = request_json(
                "GET",
                metadata["userinfo_endpoint"],
                auth=BearerToken(tokens["access_token"]),
            )
            if info.get("sub") != claims["sub"]:
                raise OIDCError("UserInfo subject mismatch")
            for field in ("name", "email", settings.oidc_organization_claim):
                if field in info:
                    if (
                        field == settings.oidc_organization_claim
                        and field in claims
                        and claims[field] != info[field]
                    ):
                        raise OIDCError("Conflicting organization claims")
                    profile[field] = info[field]
        if profile.get(settings.oidc_organization_claim) != settings.oidc_organization_id:
            raise OIDCError("Organization membership mismatch")
        return {
            "subject": claims["sub"],
            "issuer": claims["iss"],
            "organization_id": settings.oidc_organization_id,
            "roles": verified_roles(settings, claims, info),
            "name": profile.get("name") if isinstance(profile.get("name"), str) else None,
            "email": profile.get("email") if isinstance(profile.get("email"), str) else None,
            "expires_at": min(int(time.time()) + settings.admin_session_ttl_seconds, claims["exp"]),
            "policy": policy_key(settings),
            "csrf_token": secrets.token_urlsafe(32),
        }
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise OIDCError("OIDC identity validation failed") from exc


class BearerToken(httpx.Auth):
    def __init__(self, token: str):
        self.token = token

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request
