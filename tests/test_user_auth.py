"""Exercise the real OIDC/session routes against a signed mock provider, without services."""

import hashlib
import json
import secrets
import time
from base64 import urlsafe_b64encode
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from devfeed_http import oidc as oidc_protocol
from devfeed_user_api import auth
from devfeed_user_api.config import Settings
from devfeed_user_api.dependencies import get_session
from devfeed_user_api.main import create_app
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError

ISSUER = "https://identity.example"


ORIGIN = "https://user.example"


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


@pytest.fixture
def oidc_app(monkeypatch):
    from devfeed_user_api import main

    settings = Settings(
        _env_file=None,
        base_url=ORIGIN,
        oidc_issuer_url=ISSUER,
        oidc_client_id="user-client",
        oidc_organization_id="org-1",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "save_user", lambda identity: "00000000-0000-4000-8000-000000000001")
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
            assert posted["redirect_uri"] == [ORIGIN + "/api/v1/user/auth/callback"]
            claims = {
                "iss": ISSUER,
                "aud": "user-client",
                "sub": "user-1",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
                "nonce": state.params["nonce"][0],
                ORG_CLAIM: "org-1",
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
            return httpx.Response(200, json={"sub": "user-1", "name": "Admin", **state.info})
        pytest.fail("Unexpected provider endpoint")

    real_client = httpx.Client
    monkeypatch.setattr(
        oidc_protocol.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(provider), **kw),
    )
    app = create_app()
    app.dependency_overrides[get_session] = lambda: pytest.fail("Unauthorized DB access")
    with TestClient(app, base_url=ORIGIN) as client:
        state.client = client
        yield state


def begin(state, *, register=False):
    result = state.client.get(
        "/v1/user/auth/login", params={"register": register}, follow_redirects=False
    )
    assert result.status_code == 302
    state.params = parse_qs(urlsplit(result.headers["location"]).query)
    return state.params["state"][0]


def complete(state, flow=None, extra=None):
    flow = flow or begin(state)
    return state.client.get(
        "/v1/user/auth/callback?"
        + urlencode(
            {
                "code": "one-time-code",
                "state": flow,
                **(extra or {}),
            }
        ),
        follow_redirects=False,
    )


def test_pkce_discovery_org_login_session_and_logout(oidc_app):
    state = oidc_app
    flow = begin(state)
    assert state.params["code_challenge_method"] == ["S256"]
    assert "urn:zitadel:iam:org:id:org-1" in state.params["scope"][0].split()
    assert "urn:zitadel:iam:org:project:role:superuser" not in state.params["scope"][0].split()
    assert "code_verifier" not in state.params
    result = complete(state, flow)
    assert result.headers["location"] == ORIGIN + "/my-feed"
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "Secure" in result.headers["set-cookie"]
    assert "SameSite=lax" in result.headers["set-cookie"]
    assert result.headers["cache-control"] == "no-store"
    me = state.client.get("/v1/user/auth/me")
    assert me.status_code == 200
    assert me.json()["subject"] == "user-1"
    assert "roles" not in me.json()
    assert "provider-access-token" not in me.text
    token = state.client.cookies.get("__Host-devfeed_user_session")
    assert state.store.get(auth.key("session", token))
    response = state.client.post(
        "/v1/user/auth/logout",
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": me.json()["csrf_token"],
        },
    )
    assert response.status_code == 204
    assert state.store.get(auth.key("session", token)) is None
    assert state.client.get("/v1/user/auth/me").json() is None
    assert state.client.get("/v1/user/preferences").status_code == 401


def logout_headers(state):
    token = state.client.cookies.get("__Host-devfeed_user_session")
    record = json.loads(state.store.get(auth.key("session", token)))
    return {"Origin": ORIGIN, "X-CSRF-Token": record["csrf_token"]}


def test_logout_clears_both_cookies_and_prevents_session_replay(oidc_app):
    state = oidc_app
    complete(state)
    token = state.client.cookies.get("__Host-devfeed_user_session")
    response = state.client.post("/v1/user/auth/logout", headers=logout_headers(state))
    assert response.status_code == 204
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert all("Max-Age=0" in value and "HttpOnly" in value for value in cookies)
    assert any("__Host-devfeed_user_state=" in value for value in cookies)
    assert response.headers["cache-control"] == "no-store"
    state.client.cookies.set("__Host-devfeed_user_session", token)
    assert state.client.get("/v1/user/auth/me").json() is None
    assert state.client.get("/v1/user/preferences").status_code == 401


@pytest.mark.parametrize("token", [None, "malformed", "A" * 43])
def test_logout_is_idempotent_without_a_live_session(oidc_app, token):
    if token is not None:
        oidc_app.client.cookies.set("__Host-devfeed_user_session", token)
    for _ in range(2):
        response = oidc_app.client.post("/v1/user/auth/logout", headers={"Origin": ORIGIN})
        assert response.status_code == 204
        assert len(response.headers.get_list("set-cookie")) == 2


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null"])
def test_logout_requires_same_origin_even_without_a_session(oidc_app, origin):
    response = oidc_app.client.post(
        "/v1/user/auth/logout", headers={"Origin": origin} if origin else {}
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_logout_csrf_failure_does_not_revoke_the_session(oidc_app):
    complete(oidc_app)
    response = oidc_app.client.post(
        "/v1/user/auth/logout",
        headers={"Origin": ORIGIN, "X-CSRF-Token": secrets.token_urlsafe(32)},
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    assert oidc_app.client.get("/v1/user/auth/me").status_code == 200


@pytest.mark.parametrize("operation", ["get", "delete"])
def test_logout_dependency_failure_keeps_cookie_for_retry(oidc_app, monkeypatch, operation):
    state = oidc_app
    complete(state)
    headers = logout_headers(state)
    token = state.client.cookies.get("__Host-devfeed_user_session")
    monkeypatch.setattr(
        state.store, operation, lambda key: (_ for _ in ()).throw(ConnectionError())
    )
    response = state.client.post("/v1/user/auth/logout", headers=headers)
    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    assert state.client.cookies.get("__Host-devfeed_user_session") == token


def test_logout_blocks_an_outstanding_login_in_this_browser(oidc_app):
    state = oidc_app
    complete(state)
    headers = logout_headers(state)
    pending = begin(state)
    assert state.client.post("/v1/user/auth/logout", headers=headers).status_code == 204
    assert complete(state, pending).headers["location"].endswith("error=login_failed")
    assert not any(":session:" in key for key in state.store.values)


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://wrong.example"},
        {"aud": "user-app"},
        {"nonce": "wrong"},
        {"exp": 1},
        {"iat": int(time.time()) + 9999},
        {"sub": 123},
        {"aud": ["user-client", "other"]},
        {"azp": "other"},
        {ORG_CLAIM: "another-org"},
        {ORG_CLAIM: None},
    ],
)
def test_invalid_identities_cannot_open_a_session(oidc_app, claims):
    oidc_app.claims = claims
    response = complete(oidc_app)
    assert response.headers["location"].endswith("/login?error=login_failed")
    assert oidc_app.client.get("/v1/user/auth/me").json() is None
    assert oidc_app.client.get("/v1/user/preferences").status_code == 401


def test_unbound_or_replayed_callback_cannot_log_out_an_existing_admin(oidc_app):
    flow = begin(oidc_app)
    complete(oidc_app, flow)
    previous = oidc_app.client.cookies.get("__Host-devfeed_user_session")
    for invalid_state in (secrets.token_urlsafe(32), flow):
        response = complete(oidc_app, invalid_state)
        assert response.headers["location"].endswith("error=login_failed")
        assert not any(
            "admin_session=" in value for value in response.headers.get_list("set-cookie")
        )
        assert oidc_app.client.cookies.get("__Host-devfeed_user_session") == previous
        assert oidc_app.client.get("/v1/user/auth/me").status_code == 200


def test_validated_provider_cancellation_clears_previous_local_identity(oidc_app):
    complete(oidc_app)
    previous = oidc_app.client.cookies.get("__Host-devfeed_user_session")
    response = complete(oidc_app, extra={"error": "access_denied"})
    assert response.headers["location"].endswith("error=login_failed")
    assert oidc_app.store.get(auth.key("session", previous)) is None
    assert oidc_app.client.get("/v1/user/auth/me").json() is None
    assert oidc_app.client.get("/v1/user/preferences").status_code == 401


def test_callback_revocation_failure_cannot_mint_a_replacement_session(oidc_app, monkeypatch):
    state = oidc_app
    complete(state)
    previous = state.client.cookies.get("__Host-devfeed_user_session")
    previous_key = auth.key("session", previous)
    original_delete = state.store.delete

    def unavailable(key):
        if key == previous_key:
            raise ConnectionError()
        original_delete(key)

    monkeypatch.setattr(state.store, "delete", unavailable)
    response = complete(state)
    assert response.headers["location"].endswith("error=login_failed")
    assert state.client.cookies.get("__Host-devfeed_user_session") is None
    assert [key for key in state.store.values if ":session:" in key] == [previous_key]
    assert sum(request.url.path == "/token" for request in state.requests) == 1


def test_userinfo_cannot_switch_identity(oidc_app):
    oidc_app.info = {"sub": "someone-else"}
    assert complete(oidc_app).headers["location"].endswith("error=login_failed")


def test_callback_is_browser_bound_and_single_use(oidc_app):
    flow = begin(oidc_app)
    browser = oidc_app.client.cookies.get("__Host-devfeed_user_state")
    oidc_app.client.cookies.clear()
    assert complete(oidc_app, flow).headers["location"].endswith("error=login_failed")
    assert oidc_app.store.get(auth.key("flow", flow)) is not None
    oidc_app.client.cookies.set("__Host-devfeed_user_state", browser)
    assert complete(oidc_app, flow).headers["location"] == ORIGIN + "/my-feed"
    assert complete(oidc_app, flow).headers["location"].endswith("error=login_failed")
    assert sum(request.url.path == "/token" for request in oidc_app.requests) == 1


@pytest.mark.parametrize(
    "metadata",
    [
        {"issuer": "https://other.example"},
        {"token_endpoint": "http://other.example/token"},
        {"code_challenge_methods_supported": ["plain"]},
        {"token_endpoint_auth_methods_supported": ["client_secret_basic"]},
    ],
)
def test_invalid_discovery_fails_closed(oidc_app, metadata):
    oidc_app.metadata = metadata
    assert oidc_app.client.get("/v1/user/auth/login").status_code == 503


def test_random_cookie_and_state_are_rejected(oidc_app):
    oidc_app.client.cookies.set("__Host-devfeed_user_session", secrets.token_urlsafe(32))
    assert oidc_app.client.get("/v1/user/auth/me").json() is None
    assert oidc_app.client.get("/v1/user/preferences").status_code == 401
    assert (
        complete(oidc_app, secrets.token_urlsafe(32))
        .headers["location"]
        .endswith("error=login_failed")
    )


@pytest.mark.parametrize("method", ["client_secret_basic", "client_secret_post"])
def test_confidential_clients_still_use_pkce(oidc_app, method):
    from pydantic import SecretStr

    oidc_app.settings.oidc_token_endpoint_auth_method = method
    oidc_app.settings.oidc_client_secret = SecretStr("test-client-secret")
    assert complete(oidc_app).headers["location"] == ORIGIN + "/my-feed"
    request = next(r for r in oidc_app.requests if r.url.path == "/token")
    values = parse_qs(request.content.decode())
    assert "code_verifier" in values
    if method == "client_secret_basic":
        assert request.headers["authorization"].startswith("Basic ")
        assert "client_secret" not in values
    else:
        assert values["client_secret"] == ["test-client-secret"]


def test_generic_provider_organization_mapping(oidc_app):
    oidc_app.settings.oidc_organization_scope_template = "organization:{organization_id}"
    oidc_app.settings.oidc_organization_claim = "organization"
    oidc_app.claims["organization"] = "org-1"
    assert complete(oidc_app).headers["location"] == ORIGIN + "/my-feed"
    assert "organization:org-1" in oidc_app.params["scope"][0].split()


@pytest.mark.parametrize(
    "metadata",
    [
        {"code_challenge_methods_supported": None},
        {"token_endpoint_auth_methods_supported": "none"},
        {"token_endpoint": "https://["},
    ],
)
def test_malformed_discovery_is_a_controlled_error(oidc_app, metadata):
    oidc_app.metadata = metadata
    assert oidc_app.client.get("/v1/user/auth/login").status_code == 503


@pytest.mark.parametrize("claim", ["at_hash", "c_hash"])
def test_invalid_token_hash_binding_is_rejected(oidc_app, claim):
    oidc_app.claims[claim] = "invalid"
    assert complete(oidc_app).headers["location"].endswith("error=login_failed")


def test_conflicting_userinfo_organization_is_rejected(oidc_app):
    oidc_app.info[ORG_CLAIM] = "different-org"
    assert complete(oidc_app).headers["location"].endswith("error=login_failed")


def test_expired_login_and_session_are_rejected(oidc_app):
    flow = begin(oidc_app)
    key = auth.key("flow", flow)
    raw, _ = oidc_app.store.values[key]
    oidc_app.store.values[key] = (raw, 1)
    assert complete(oidc_app, flow).headers["location"].endswith("error=login_failed")
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_user_session")
    key = auth.key("session", token)
    raw, expiry = oidc_app.store.values[key]
    record = json.loads(raw)
    record["expires_at"] = 1
    oidc_app.store.values[key] = (json.dumps(record).encode(), expiry)
    assert oidc_app.client.get("/v1/user/auth/me").json() is None
    assert oidc_app.client.get("/v1/user/preferences").status_code == 401


def test_registration_is_explicit_and_never_requests_admin_role(oidc_app):
    begin(oidc_app, register=True)
    assert oidc_app.params["prompt"] == ["create"]
    assert "superuser" not in oidc_app.params["scope"][0]
    assert "project:role" not in oidc_app.params["scope"][0]
    assert complete(oidc_app, oidc_app.params["state"][0]).headers["location"].endswith("/my-feed")


def test_user_session_does_not_accept_admin_cookie_or_namespace(oidc_app):
    from devfeed_admin_api import auth as admin_auth

    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_user_session")
    record = oidc_app.store.get(auth.key("session", token))
    oidc_app.client.cookies.clear()
    oidc_app.client.cookies.set("__Host-devfeed_admin_session", token)
    assert oidc_app.client.get("/v1/user/auth/me").json() is None
    assert oidc_app.client.get("/v1/user/preferences").status_code == 401
    oidc_app.client.cookies.set("__Host-devfeed_user_session", token)
    oidc_app.store.delete(auth.key("session", token))
    oidc_app.store.set(admin_auth.key("session", token), record.decode(), ex=3600)
    assert oidc_app.client.get("/v1/user/auth/me").json() is None
    assert oidc_app.client.get("/v1/user/preferences").status_code == 401
    assert oidc_app.client.get("/v1/admin/auth/me").status_code == 404


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "preferences"),
        ("PUT", "preferences"),
        ("GET", "feed"),
        ("PUT", "preferences/topics/00000000-0000-0000-0000-000000000001"),
    ],
)
def test_personalization_requires_session_and_csrf_before_database(oidc_app, method, path):
    payload = {"followed": True} if "/topics/" in path else {"topic_ids": []}
    response = oidc_app.client.request(
        method, "/v1/user/" + path, json=payload if method == "PUT" else None
    )
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    if method == "PUT":
        complete(oidc_app)
        response = oidc_app.client.put("/v1/user/" + path, json=payload)
        assert response.status_code == 403


def test_user_client_change_revokes_sessions(oidc_app):
    complete(oidc_app)
    oidc_app.settings.oidc_client_id = "changed-client"
    assert oidc_app.client.get("/v1/user/auth/me").json() is None
    assert oidc_app.client.get("/v1/user/preferences").status_code == 401


def test_account_storage_failure_does_not_create_session(oidc_app, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    def fail(identity):
        raise SQLAlchemyError("private database details")

    monkeypatch.setattr(auth, "save_user", fail)
    result = complete(oidc_app)
    assert result.headers["location"].endswith("/login?error=login_failed")
    assert oidc_app.client.get("/v1/user/auth/me").json() is None
    assert oidc_app.client.get("/v1/user/preferences").status_code == 401
    assert "private database" not in result.text


def test_likes_require_sign_in_and_csrf_before_database(oidc_app):
    path = "/v1/user/articles/00000000-0000-4000-8000-000000000001/like"
    assert oidc_app.client.put(path, json={"liked": True}).status_code == 401
    complete(oidc_app)
    assert oidc_app.client.put(path, json={"liked": True}).status_code == 403
    assert (
        oidc_app.client.put(
            path,
            json={"liked": True},
            headers={
                "Origin": "https://evil.example",
                "X-CSRF-Token": logout_headers(oidc_app)["X-CSRF-Token"],
            },
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "destination",
    [
        "https://evil.example",
        "//evil.example",
        "/\\evil.example",
        "/my-feed?next=https://evil.example",
        "/admin",
        "/preferences",
    ],
)
def test_sign_in_rejects_external_or_unknown_return_paths(oidc_app, destination):
    assert (
        oidc_app.client.get(
            "/v1/user/auth/login", params={"return_to": destination}, follow_redirects=False
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "destination",
    [
        "/articles/00000000-0000-4000-8000-000000000001",
        "/articles/optimizing-docker-images-142",
        "/settings/topics",
        "/settings/appearance",
        "/settings/sources",
        "/sources",
        "/sources/00000000-0000-4000-8000-000000000001",
    ],
)
def test_sign_in_returns_to_the_page_that_prompted_login(oidc_app, destination):
    result = oidc_app.client.get(
        "/v1/user/auth/login", params={"return_to": destination}, follow_redirects=False
    )
    oidc_app.params = parse_qs(urlsplit(result.headers["location"]).query)
    result = complete(oidc_app, oidc_app.params["state"][0])
    assert result.headers["location"] == ORIGIN + destination


def test_anonymous_session_probe_is_successful_and_private_routes_stay_protected(oidc_app):
    response = oidc_app.client.get("/v1/user/auth/me")
    assert response.status_code == 200
    assert response.json() is None
    assert response.headers["cache-control"] == "no-store"
    assert oidc_app.client.get("/v1/user/preferences").status_code == 401


def test_session_probe_does_not_hide_session_store_outages(oidc_app, monkeypatch):
    from redis.exceptions import RedisError

    complete(oidc_app)

    def unavailable(key):
        raise RedisError("unavailable")

    monkeypatch.setattr(oidc_app.store, "get", unavailable)
    assert oidc_app.client.get("/v1/user/auth/me").status_code == 503


@pytest.mark.parametrize("section", ["profile", "notifications", "appearance"])
def test_settings_require_session_and_csrf_before_database(oidc_app, section):
    path = f"/v1/user/settings/{section}"
    assert oidc_app.client.get(path).status_code == 401
    assert oidc_app.client.put(path, json={}).status_code == 401
    complete(oidc_app)
    assert oidc_app.client.put(path, json={}).status_code == 403


def test_source_follows_require_session_and_csrf_before_database(oidc_app):
    path = "/v1/user/preferences/sources/00000000-0000-4000-8000-000000000001"
    assert oidc_app.client.get("/v1/user/preferences/sources").status_code == 401
    assert oidc_app.client.put(path, json={"followed": True}).status_code == 401
    complete(oidc_app)
    assert oidc_app.client.put(path, json={"followed": True}).status_code == 403
    assert (
        oidc_app.client.put("/v1/user/preferences/sources", json={"source_ids": []}).status_code
        == 403
    )
