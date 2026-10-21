"""Exercise the real OIDC/session routes against a signed mock provider, without services."""

import hashlib
import json
import os
import time
from base64 import urlsafe_b64encode
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from devfeed_http import oidc as oidc_protocol
from devfeed_partner_api import auth
from devfeed_partner_api.config import Settings
from devfeed_partner_api.dependencies import get_session
from devfeed_partner_api.main import create_app
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

ISSUER = "https://identity.example"
ORIGIN = "https://partner.example"
ORG_CLAIM = "urn:zitadel:iam:user:resourceowner:id"
ROLE_CLAIM = "urn:zitadel:iam:org:project:roles"


class SessionStore:
    def __init__(self):
        self.values = {}

    def set(self, key, value, ex):
        self.values[key] = (value.encode(), time.time() + ex)
        return True

    def get(self, key):
        value, expiry = self.values.get(key, (None, 0))
        return value if expiry > time.time() else None

    def getdel(self, key):
        value = self.get(key)
        self.delete(key)
        return value

    def delete(self, key):
        self.values.pop(key, None)

    def eval(self, script, count, key, expected, replacement, version):
        assert script == auth.REGISTERED_SESSION and count == 1
        current = self.get(key)
        if current is None:
            return 0
        if json.loads(current).get("identity_registration_version") == version:
            return 1
        if current != expected:
            return 0
        self.values[key] = (replacement.encode(), self.values[key][1])
        return 1


@pytest.fixture
def oidc_app(monkeypatch):
    from devfeed_partner_api import main

    settings = Settings(
        _env_file=None,
        partner_base_url=ORIGIN,
        oidc_issuer_url=ISSUER,
        oidc_client_id="partner-client",
        oidc_organization_id="org-1",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    store = SessionStore()
    monkeypatch.setattr(auth, "get_redis", lambda: store)
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    public.update(kid="key-1", use="sig", alg="RS256")
    state = SimpleNamespace(
        settings=settings,
        store=store,
        params={},
        claims={},
        info={},
        requests=[],
        metadata={},
        omit_claims=set(),
        key=signing_key,
        public=public,
    )

    def provider(request):
        state.requests.append(request)
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": ISSUER + "/authorize",
                    "token_endpoint": ISSUER + "/token",
                    "jwks_uri": ISSUER + "/keys",
                    "userinfo_endpoint": ISSUER + "/userinfo",
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": [
                        "none",
                        "client_secret_basic",
                        "client_secret_post",
                    ],
                    **state.metadata,
                },
            )
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": [state.public]})
        if request.url.path == "/token":
            posted = parse_qs(request.content.decode())
            verifier = posted["code_verifier"][0]
            actual = (
                urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            )
            assert actual == state.params["code_challenge"][0]
            assert posted["redirect_uri"] == [ORIGIN + "/api/v1/partner/auth/callback"]
            claims = {
                "iss": ISSUER,
                "aud": "partner-client",
                "sub": "partner-1",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
                "nonce": state.params["nonce"][0],
                ORG_CLAIM: "org-1",
                ROLE_CLAIM: {"partner": {"org-1": "org.example"}},
                **state.claims,
            }
            if state.params.get("max_age") == ["0"]:
                claims.setdefault("auth_time", int(time.time()))
            for name in state.omit_claims:
                claims.pop(name, None)
            return httpx.Response(
                200,
                json={
                    "id_token": jwt.encode(
                        claims, state.key, algorithm="RS256", headers={"kid": "key-1"}
                    ),
                    "access_token": "provider-access-token",
                },
            )
        if request.url.path == "/userinfo":
            assert request.headers["authorization"] == "Bearer provider-access-token"
            return httpx.Response(200, json={"sub": "partner-1", "name": "Partner", **state.info})
        pytest.fail("Unexpected provider endpoint")

    real_client = httpx.Client
    monkeypatch.setattr(
        oidc_protocol.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(provider), **kw),
    )
    state.register_user = Mock(return_value="known-user")
    monkeypatch.setattr(auth, "register_verified_user", state.register_user)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: pytest.fail("Unauthorized DB access")
    with TestClient(app, base_url=ORIGIN) as client:
        state.client = client
        yield state


def begin(state, *, reauthenticate=False):
    result = state.client.get(
        "/v1/partner/auth/login", params={"reauthenticate": reauthenticate}, follow_redirects=False
    )
    assert result.status_code == 302
    state.params = parse_qs(urlsplit(result.headers["location"]).query)
    return state.params["state"][0]


def complete(state, flow=None, extra=None):
    flow = flow or begin(state)
    return state.client.get(
        "/v1/partner/auth/callback?"
        + urlencode(
            {
                "code": "one-time-code",
                "state": flow,
                **(extra or {}),
            }
        ),
        follow_redirects=False,
    )


@pytest.mark.parametrize(
    "suffix",
    [
        "connector-preview",
        "connector-response",
        "saved-connection/connector-preview",
        "saved-connection/connector-response",
    ],
)
def test_import_previews_require_session_and_csrf_before_database_access(oidc_app, suffix):
    state = oidc_app
    url = f"/v1/partner/accounts/00000000-0000-0000-0000-000000000001/connections/{suffix}"
    body = {"connector": {"base_url": "https://catalog.example"}, "expected_revision": 1}
    assert state.client.post(url, json=body).status_code == 401
    assert complete(state).status_code == 302
    for headers in ({}, {"Origin": ORIGIN}, {"Origin": "https://attacker.example"}):
        assert state.client.post(url, json=body, headers=headers).status_code == 403


def test_product_image_upload_requires_session_and_csrf_before_database_access(oidc_app):
    state = oidc_app
    url = (
        "/v1/partner/accounts/00000000-0000-0000-0000-000000000001"
        "/products/00000000-0000-0000-0000-000000000002/ad-image"
    )
    files = {"file": ("image.png", b"not decoded before authorization", "image/png")}
    assert state.client.post(url, files=files).status_code == 401
    assert complete(state).status_code == 302
    for headers in ({}, {"Origin": ORIGIN}, {"Origin": "https://attacker.example"}):
        assert state.client.post(url, files=files, headers=headers).status_code == 403


@pytest.mark.parametrize("role", ["partner"])
def test_partner_login_requests_partner_role_and_isolates_sessions(oidc_app, role):
    state = oidc_app
    state.claims[ROLE_CLAIM] = {role: {"org-1": "org.example"}}
    flow = begin(state)
    scopes = state.params["scope"][0].split()
    assert "urn:zitadel:iam:org:project:role:partner" in scopes
    assert "urn:zitadel:iam:org:project:role:superuser" not in scopes
    result = complete(state, flow)
    assert result.headers["location"] == ORIGIN + "/"
    assert "HttpOnly" in result.headers["set-cookie"]
    me = state.client.get("/v1/partner/auth/me")
    assert me.status_code == 200
    assert me.json()["roles"] == [role]
    token = state.client.cookies.get("__Host-devfeed_partner_session")
    assert state.store.get(auth.key("session", token))
    assert not state.store.get(
        "devfeed:admin:session:" + hashlib.sha256(token.encode()).hexdigest()
    )
    assert (
        state.client.post("/v1/partner/auth/logout", headers={"Origin": ORIGIN}).status_code == 403
    )
    assert (
        state.client.post(
            "/v1/partner/auth/logout",
            headers={"Origin": ORIGIN, "X-CSRF-Token": me.json()["csrf_token"]},
        ).status_code
        == 204
    )
    assert state.client.get("/v1/partner/auth/me").status_code == 401


@pytest.mark.parametrize(
    "claims,info",
    [
        ({ROLE_CLAIM: {}}, {}),
        ({ROLE_CLAIM: {"partner": {"other-org": "other.example"}}}, {}),
        (
            {ROLE_CLAIM: {"partner": {"org-1": "org.example"}}},
            {ROLE_CLAIM: {"superuser": {"org-1": "org.example"}}},
        ),
        ({ORG_CLAIM: "other-org"}, {}),
    ],
)
def test_untrusted_or_conflicting_grants_cannot_enter(oidc_app, claims, info):
    oidc_app.claims.update(claims)
    oidc_app.info.update(info)
    assert urlsplit(complete(oidc_app).headers["location"]).path == "/login"
    assert oidc_app.client.get("/v1/partner/auth/me").status_code == 401


def test_session_rechecks_role_policy_and_csrf(oidc_app):
    state = oidc_app
    complete(state)
    me = state.client.get("/v1/partner/auth/me").json()
    for headers in ({}, {"Origin": "https://evil.example", "X-CSRF-Token": me["csrf_token"]}):
        assert state.client.post("/v1/partner/auth/logout", headers=headers).status_code == 403
    # The partner service never exposes account management, even with valid CSRF.
    assert (
        state.client.post(
            "/v1/partner/accounts",
            json={"name": "A", "tier": "bronze"},
            headers={"Origin": ORIGIN, "X-CSRF-Token": me["csrf_token"]},
        ).status_code
        == 405
    )
    state.settings.oidc_client_id = "changed-client"
    assert state.client.get("/v1/partner/auth/me").status_code == 401


def test_callback_replay_and_admin_cookie_do_not_authenticate(oidc_app):
    state = oidc_app
    flow = begin(state)
    assert complete(state, flow).headers["location"] == ORIGIN + "/"
    assert urlsplit(complete(state, flow).headers["location"]).path == "/login"
    token = state.client.cookies.get("__Host-devfeed_partner_session")
    state.client.cookies.clear()
    state.client.cookies.set("__Host-devfeed_admin_session", token)
    assert state.client.get("/v1/partner/auth/me").status_code == 401


@pytest.mark.parametrize("roles", [[], ["reader"], ["superuser"]])
def test_only_partner_role_can_complete_login(oidc_app, roles):
    state = oidc_app
    state.claims[ROLE_CLAIM] = {role: {"org-1": "org.example"} for role in roles}
    result = complete(state)
    assert urlsplit(result.headers["location"]).path == "/login"
    assert parse_qs(urlsplit(result.headers["location"]).query)["error"] == ["access_denied"]
    assert not state.client.cookies.get("__Host-devfeed_partner_session")
    state.register_user.assert_not_called()
    assert state.client.get("/v1/partner/auth/me").status_code == 401


def test_partner_role_remains_required_for_an_existing_session(oidc_app):
    state = oidc_app
    complete(state)
    token = state.client.cookies.get("__Host-devfeed_partner_session")
    key = auth.key("session", token)
    record = json.loads(state.store.get(key))
    record["roles"] = ["superuser"]
    state.store.set(key, json.dumps(record), ex=3600)
    assert state.client.get("/v1/partner/auth/me").status_code == 403
    assert state.client.get("/v1/partner/accounts").status_code == 403


def test_partner_with_additional_superuser_role_can_login(oidc_app):
    state = oidc_app
    state.claims[ROLE_CLAIM] = {role: {"org-1": "org.example"} for role in ("partner", "superuser")}
    assert complete(state).headers["location"] == ORIGIN + "/"
    assert state.client.get("/v1/partner/auth/me").status_code == 200


def test_verified_partner_login_registers_user_for_member_selection(oidc_app):
    response = complete(oidc_app, begin(oidc_app))
    assert response.status_code == 302
    assert oidc_app.register_user.call_count == 1
    identity = oidc_app.register_user.call_args.args[0]
    assert identity["subject"] == "partner-1"
    assert identity["issuer"] == ISSUER
    assert "partner" in identity["roles"]
    assert oidc_app.client.get("/v1/partner/auth/me").status_code == 200
    assert oidc_app.client.get("/v1/partner/auth/me").status_code == 200
    assert oidc_app.register_user.call_count == 1
    assert "identity_registration_version" not in oidc_app.client.get("/v1/partner/auth/me").json()


def legacy_session(state):
    assert complete(state).status_code == 302
    token = state.client.cookies.get("__Host-devfeed_partner_session")
    session_key = auth.key("session", token)
    record = json.loads(state.store.get(session_key))
    record.pop("identity_registration_version")
    state.store.set(session_key, json.dumps(record), ex=3600)
    state.register_user.reset_mock()
    return session_key


def test_legacy_partner_identity_is_registered_once_without_extending_session(oidc_app):
    session_key = legacy_session(oidc_app)
    expiry = oidc_app.store.values[session_key][1]
    for _ in range(3):
        assert oidc_app.client.get("/v1/partner/auth/me").status_code == 200
    oidc_app.register_user.assert_called_once()
    assert oidc_app.store.values[session_key][1] == expiry
    record = json.loads(oidc_app.store.get(session_key))
    assert record["identity_registration_version"] == auth.IDENTITY_REGISTRATION_VERSION


@pytest.mark.parametrize("failure", [SQLAlchemyError("unavailable"), RedisError("unavailable")])
def test_legacy_registration_failures_remain_retryable(oidc_app, monkeypatch, failure):
    session_key = legacy_session(oidc_app)
    if isinstance(failure, SQLAlchemyError):
        oidc_app.register_user.side_effect = failure
    else:
        monkeypatch.setattr(oidc_app.store, "eval", Mock(side_effect=failure))
    assert oidc_app.client.get("/v1/partner/auth/me").status_code == 503
    assert "identity_registration_version" not in json.loads(oidc_app.store.get(session_key))


def test_legacy_registration_cannot_restore_a_logged_out_session(oidc_app):
    session_key = legacy_session(oidc_app)
    oidc_app.register_user.side_effect = lambda _identity: oidc_app.store.delete(session_key)
    assert oidc_app.client.get("/v1/partner/auth/me").status_code == 503
    assert oidc_app.store.get(session_key) is None
    assert oidc_app.client.get("/v1/partner/auth/me").status_code == 401


@pytest.mark.integration
def test_registration_receipt_preserves_real_redis_expiry_and_logout(oidc_app, monkeypatch):
    from redis import Redis
    from test_oidc_logout import IsolatedRedis

    redis_url = os.environ.get("DEVFEED_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Set disposable DEVFEED_TEST_REDIS_URL (database 15)")
    assert urlsplit(redis_url).path == "/15"
    logout_redis = IsolatedRedis(Redis.from_url(redis_url))
    try:
        verify_registration_receipt(oidc_app, logout_redis, monkeypatch)
    finally:
        logout_redis.close()


def verify_registration_receipt(oidc_app, logout_redis, monkeypatch):
    oidc_app.store = logout_redis
    monkeypatch.setattr(auth, "get_redis", lambda: logout_redis)
    session_key = legacy_session(oidc_app)
    ttl = logout_redis.redis.pttl(logout_redis.prefix + session_key)
    for _ in range(3):
        assert oidc_app.client.get("/v1/partner/auth/me").status_code == 200
    oidc_app.register_user.assert_called_once()
    assert 0 < logout_redis.redis.pttl(logout_redis.prefix + session_key) <= ttl
    assert json.loads(logout_redis.get(session_key))["identity_registration_version"] == 1

    oidc_app.register_user.side_effect = None
    session_key = legacy_session(oidc_app)
    oidc_app.register_user.side_effect = lambda _identity: logout_redis.delete(session_key)
    assert oidc_app.client.get("/v1/partner/auth/me").status_code == 503
    assert logout_redis.get(session_key) is None
    assert oidc_app.client.get("/v1/partner/auth/me").status_code == 401
