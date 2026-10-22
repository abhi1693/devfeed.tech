"""OIDC rejects malformed transport and identities before trusting provider context.

JWT signing and parsing are real. Redis methods supply read/consume outcomes;
these units do not claim compare-and-delete atomicity or service authorization.
"""

import hashlib
import json
import time
from base64 import urlsafe_b64encode
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import jwt
import pytest
from devfeed_http import oidc
from devfeed_user_api.config import Settings
from starlette.datastructures import QueryParams
from test_oidc_logout import signed as signed

STATE, BROWSER = "s" * 43, "b" * 43
NONCE, VERIFIER = "n" * 43, "v" * 43
ORG = "urn:zitadel:iam:user:resourceowner:id"


@pytest.mark.parametrize("field", ["state", "code", "error", "iss"])
def test_ambiguous_callback_fields_are_rejected_before_reading_login_context(field):
    redis = Mock()
    with pytest.raises(oidc.OIDCError, match="Ambiguous callback"):
        oidc.consume_callback_flow(
            query_params=QueryParams([(field, "first"), (field, "second")]),
            state=STATE,
            browser=BROWSER,
            redis=redis,
            flow_key="admin:flow",
            policy="current",
        )
    redis.get.assert_not_called()
    redis.getdel.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "state",
        "browser",
        "missing",
        "invalid-json",
        "invalid-shape",
        "other-browser",
        "policy",
        "already-used",
    ],
)
def test_callback_binding_and_consumption_failures_never_return_reusable_login_secrets(change):
    redis = Mock()
    flow = {
        "browser": BROWSER,
        "policy": "current",
        "nonce": "private-nonce",
        "verifier": "private-verifier",
    }
    raw = json.dumps(flow)
    if change == "missing":
        raw = None
    elif change == "invalid-json":
        raw = "{"
    elif change == "invalid-shape":
        raw = "[]"
    elif change == "other-browser":
        raw = json.dumps({**flow, "browser": "x" * 43})
    elif change == "policy":
        raw = json.dumps({**flow, "policy": "old-client"})
    redis.get.return_value = raw
    redis.getdel.return_value = None if change == "already-used" else raw
    with pytest.raises(oidc.OIDCError) as failure:
        oidc.consume_callback_flow(
            query_params=QueryParams(),
            state="short" if change == "state" else STATE,
            browser="invalid" if change == "browser" else BROWSER,
            redis=redis,
            flow_key="admin:flow",
            policy="current",
        )
    assert "private" not in str(failure.value)
    if change in {"state", "browser"}:
        redis.get.assert_not_called()
    if change != "already-used":
        redis.getdel.assert_not_called()


def test_bound_flow_returns_the_exact_document_only_after_successful_consumption():
    redis = Mock()
    flow = {"browser": BROWSER, "policy": "current", "nonce": NONCE, "verifier": VERIFIER}
    redis.get.return_value = redis.getdel.return_value = json.dumps(flow)
    assert (
        oidc.consume_callback_flow(
            query_params=QueryParams(),
            state=STATE,
            browser=BROWSER,
            redis=redis,
            flow_key="admin:flow",
            policy="current",
        )
        == flow
    )
    redis.getdel.assert_called_once_with("admin:flow")


def settings(**changes):
    return Settings(
        _env_file=None,
        **{
            "base_url": "https://user.example",
            "oidc_issuer_url": "https://issuer.test",
            "oidc_client_id": "reader",
            "oidc_organization_id": "org",
            **changes,
        },
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        None,
        "https://[broken",
        "https://owner:private@issuer.test/token",
        "http://other.example/token",
        "https://issuer.test/token#fragment",
    ],
)
def test_discovery_endpoint_rejects_non_urls_credentials_and_unsafe_transport(endpoint):
    with pytest.raises(oidc.OIDCError, match="Invalid discovery endpoint") as failure:
        oidc.endpoint(endpoint, settings())
    assert "private" not in str(failure.value)


@pytest.mark.parametrize("kind", ["array", "invalid-json", "oversized", "redirect", "unavailable"])
def test_provider_transport_has_bounded_json_and_does_not_follow_secret_bearing_redirects(
    monkeypatch, kind
):
    calls = []

    def respond(request):
        calls.append(request)
        if kind == "unavailable":
            raise httpx.ConnectError("private provider endpoint", request=request)
        if kind == "array":
            return httpx.Response(200, json=["private provider profile"])
        if kind == "invalid-json":
            return httpx.Response(200, content=b"private invalid JSON")
        if kind == "oversized":
            return httpx.Response(200, content=b'{"data":"' + b"x" * 1_000_000 + b'"}')
        return httpx.Response(
            302, headers={"location": "https://attacker.example/?credential=private"}
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        oidc.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)
    )
    with pytest.raises(oidc.OIDCError) as failure:
        oidc.request_json("POST", "https://issuer.test/token", data={"code": "private-code"})
    assert "private" not in str(failure.value) and len(calls) == 1
    assert calls[0].url.host == "issuer.test" and calls[0].headers["accept"] == "application/json"


def test_provider_may_omit_pkce_advertisement_but_client_still_uses_s256(monkeypatch):
    config = settings()
    metadata = {
        "issuer": config.oidc_issuer_url,
        "authorization_endpoint": "https://issuer.test/authorize",
        "token_endpoint": "https://issuer.test/token",
        "jwks_uri": "https://issuer.test/keys",
        "token_endpoint_auth_methods_supported": ["none"],
    }
    monkeypatch.setattr(oidc, "request_json", lambda *_args, **_kw: metadata)
    validated = oidc.discovery(config)
    url, flow = oidc.start(
        config, validated, redirect_uri="https://user.example/callback", policy="client-policy"
    )
    params = QueryParams(url.split("?", 1)[1])
    expected = (
        urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest()).rstrip(b"=").decode()
    )
    assert params["code_challenge_method"] == "S256" and params["code_challenge"] == expected
    assert flow["policy"] == "client-policy" and params["state"] == flow["state"]


@pytest.fixture
def identity_provider(signed, monkeypatch):
    config = settings()
    now = int(time.time())
    value = SimpleNamespace(
        settings=config,
        metadata={
            "token_endpoint": "https://issuer.test/token",
            "jwks_uri": "https://issuer.test/keys",
        },
        claims={
            "iss": config.oidc_issuer_url,
            "aud": config.oidc_client_id,
            "sub": "reader-subject",
            "iat": now,
            "exp": now + 300,
            "nonce": NONCE,
            ORG: "org",
        },
        key=signed.key,
        public=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signed.key.public_key())),
        access_token="access-token",
        info={"sub": "reader-subject"},
        requests=[],
    )
    value.public.update(kid="signing-key", use="sig", alg="RS256")
    value.keys = [value.public]

    def request(method, url, **kwargs):
        value.requests.append((method, url, kwargs))
        if method == "POST":
            return {
                "id_token": jwt.encode(
                    value.claims, value.key, algorithm="RS256", headers={"kid": "signing-key"}
                ),
                "access_token": value.access_token,
            }
        return value.info if url.endswith("/userinfo") else {"keys": value.keys}

    monkeypatch.setattr(oidc, "request_json", request)
    return value


def identify(value):
    return oidc.identity(
        value.settings,
        value.metadata,
        {"nonce": NONCE, "verifier": VERIFIER},
        "authorization-code",
        redirect_uri="https://user.example/callback",
        session_ttl=60,
    )


@pytest.mark.parametrize("change", ["at-hash-type", "c-hash-type", "missing-access-token"])
def test_signed_hash_claims_require_the_correct_typed_exchange_values(identity_provider, change):
    value = identity_provider
    value.claims["c_hash" if change == "c-hash-type" else "at_hash"] = (
        False if change != "missing-access-token" else "signed-hash"
    )
    if change == "missing-access-token":
        value.access_token = None
    with pytest.raises(oidc.OIDCError, match="Invalid token hash claim"):
        identify(value)
    assert all(not url.endswith("/userinfo") for _, url, _ in value.requests)


def test_encryption_only_jwk_is_ignored_when_a_unique_signing_key_is_available(identity_provider):
    value = identity_provider
    value.keys.insert(0, {**value.public, "kid": "encryption-only", "use": "enc"})
    result = identify(value)
    assert result["subject"] == "reader-subject" and result["expires_at"] <= int(time.time()) + 60
    assert result["provider_session"]["client_id"] == "reader"


def test_userinfo_cannot_override_security_claims_or_clear_missing_optional_profile_fields(
    identity_provider,
):
    value = identity_provider
    value.metadata["userinfo_endpoint"] = "https://issuer.test/userinfo"
    value.claims.update(name="Token name", email="reader@example.test")
    value.info.update(
        iss="https://attacker.example", aud="another-client", nonce="attacker-nonce", exp=0
    )
    result = identify(value)
    assert result["name"] == "Token name" and result["email"] == "reader@example.test"
    assert (
        result["issuer"] == value.settings.oidc_issuer_url and result["subject"] == "reader-subject"
    )
    assert result["id_claims"]["nonce"] == NONCE and result["expires_at"] > time.time()
