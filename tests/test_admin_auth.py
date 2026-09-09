"""Exercise the real OIDC/session routes against a signed mock provider, without services."""

import hashlib
import json
import secrets
import time
import uuid
from base64 import urlsafe_b64encode
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from devfeed_admin_api import auth, oidc
from devfeed_admin_api.config import Settings
from devfeed_admin_api.dependencies import get_session
from devfeed_admin_api.main import create_app
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError

ISSUER = "https://identity.example"
ORIGIN = "https://admin.example"
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
    from devfeed_admin_api import main

    settings = Settings(
        _env_file=None,
        admin_base_url=ORIGIN,
        oidc_issuer_url=ISSUER,
        oidc_client_id="admin-client",
        oidc_organization_id="org-1",
        admin_required_role="superuser",
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
            assert posted["redirect_uri"] == [ORIGIN + "/api/v1/admin/auth/callback"]
            claims = {
                "iss": ISSUER,
                "aud": "admin-client",
                "sub": "admin-1",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
                "nonce": state.params["nonce"][0],
                ORG_CLAIM: "org-1",
                ROLE_CLAIM: {"superuser": {"org-1": "org.example"}},
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
            return httpx.Response(200, json={"sub": "admin-1", "name": "Admin", **state.info})
        pytest.fail("Unexpected provider endpoint")

    real_client = httpx.Client
    monkeypatch.setattr(
        oidc.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(provider), **kw),
    )
    app = create_app()
    app.dependency_overrides[get_session] = lambda: pytest.fail("Unauthorized DB access")
    with TestClient(app, base_url=ORIGIN) as client:
        state.client = client
        yield state


def begin(state, *, reauthenticate=False):
    result = state.client.get(
        "/v1/admin/auth/login", params={"reauthenticate": reauthenticate}, follow_redirects=False
    )
    assert result.status_code == 302
    state.params = parse_qs(urlsplit(result.headers["location"]).query)
    return state.params["state"][0]


def complete(state, flow=None, extra=None):
    flow = flow or begin(state)
    return state.client.get(
        "/v1/admin/auth/callback?"
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
    assert "urn:zitadel:iam:org:project:role:superuser" in state.params["scope"][0].split()
    assert "code_verifier" not in state.params
    result = complete(state, flow)
    assert result.headers["location"] == ORIGIN + "/"
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "Secure" in result.headers["set-cookie"]
    assert "SameSite=lax" in result.headers["set-cookie"]
    assert result.headers["cache-control"] == "no-store"
    me = state.client.get("/v1/admin/auth/me")
    assert me.status_code == 200
    assert me.json()["subject"] == "admin-1"
    assert me.json()["roles"] == ["superuser"]
    assert "provider-access-token" not in me.text
    token = state.client.cookies.get("__Host-devfeed_admin_session")
    assert state.store.get(auth.key("session", token))
    response = state.client.post(
        "/v1/admin/auth/logout",
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": me.json()["csrf_token"],
        },
    )
    assert response.status_code == 204
    assert state.store.get(auth.key("session", token)) is None
    assert state.client.get("/v1/admin/auth/me").status_code == 401


def logout_headers(state):
    token = state.client.cookies.get("__Host-devfeed_admin_session")
    record = json.loads(state.store.get(auth.key("session", token)))
    return {"Origin": ORIGIN, "X-CSRF-Token": record["csrf_token"]}


def test_logout_clears_both_cookies_and_prevents_session_replay(oidc_app):
    state = oidc_app
    complete(state)
    token = state.client.cookies.get("__Host-devfeed_admin_session")
    response = state.client.post("/v1/admin/auth/logout", headers=logout_headers(state))
    assert response.status_code == 204
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert all("Max-Age=0" in value and "HttpOnly" in value for value in cookies)
    assert any("__Host-devfeed_admin_state=" in value for value in cookies)
    assert response.headers["cache-control"] == "no-store"
    state.client.cookies.set("__Host-devfeed_admin_session", token)
    assert state.client.get("/v1/admin/auth/me").status_code == 401


@pytest.mark.parametrize("change", ["role", "policy", "expiry", "old-session"])
def test_logout_does_not_require_a_still_authorized_session(oidc_app, change):
    state = oidc_app
    complete(state)
    headers = logout_headers(state)
    token = state.client.cookies.get("__Host-devfeed_admin_session")
    session_key = auth.key("session", token)
    record = json.loads(state.store.get(session_key))
    if change == "role":
        record["roles"] = []
    elif change == "policy":
        state.settings.admin_required_role = "new-role"
    elif change == "expiry":
        record["expires_at"] = 1
    else:
        del record["roles"]
    state.store.set(session_key, json.dumps(record), ex=3600)
    assert state.client.get("/v1/admin/auth/me").status_code in {401, 403}
    assert state.client.post("/v1/admin/auth/logout", headers=headers).status_code == 204
    assert state.store.get(session_key) is None


@pytest.mark.parametrize("token", [None, "malformed", "A" * 43])
def test_logout_is_idempotent_without_a_live_session(oidc_app, token):
    if token is not None:
        oidc_app.client.cookies.set("__Host-devfeed_admin_session", token)
    for _ in range(2):
        response = oidc_app.client.post("/v1/admin/auth/logout", headers={"Origin": ORIGIN})
        assert response.status_code == 204
        assert len(response.headers.get_list("set-cookie")) == 2


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null"])
def test_logout_requires_same_origin_even_without_a_session(oidc_app, origin):
    response = oidc_app.client.post(
        "/v1/admin/auth/logout", headers={"Origin": origin} if origin else {}
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_logout_csrf_failure_does_not_revoke_the_session(oidc_app):
    complete(oidc_app)
    response = oidc_app.client.post(
        "/v1/admin/auth/logout",
        headers={"Origin": ORIGIN, "X-CSRF-Token": secrets.token_urlsafe(32)},
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    assert oidc_app.client.get("/v1/admin/auth/me").status_code == 200


@pytest.mark.parametrize("operation", ["get", "delete"])
def test_logout_dependency_failure_keeps_cookie_for_retry(oidc_app, monkeypatch, operation):
    state = oidc_app
    complete(state)
    headers = logout_headers(state)
    token = state.client.cookies.get("__Host-devfeed_admin_session")
    monkeypatch.setattr(
        state.store, operation, lambda key: (_ for _ in ()).throw(ConnectionError())
    )
    response = state.client.post("/v1/admin/auth/logout", headers=headers)
    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    assert state.client.cookies.get("__Host-devfeed_admin_session") == token


def test_logout_blocks_an_outstanding_login_in_this_browser(oidc_app):
    state = oidc_app
    complete(state)
    headers = logout_headers(state)
    pending = begin(state)
    assert state.client.post("/v1/admin/auth/logout", headers=headers).status_code == 204
    assert complete(state, pending).headers["location"].endswith("error=login_failed")
    assert not any(":session:" in key for key in state.store.values)


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://wrong.example"},
        {"aud": "reader-app"},
        {"nonce": "wrong"},
        {"exp": 1},
        {"iat": int(time.time()) + 9999},
        {"sub": 123},
        {"aud": ["admin-client", "other"]},
        {"azp": "other"},
        {ORG_CLAIM: "another-org"},
        {ORG_CLAIM: None},
    ],
)
def test_invalid_identities_cannot_open_a_session(oidc_app, claims):
    oidc_app.claims = claims
    response = complete(oidc_app)
    assert response.headers["location"].endswith("/login?error=login_failed")
    assert oidc_app.client.get("/v1/admin/auth/me").status_code == 401


def test_role_check_denies_an_authenticated_non_admin(oidc_app):
    oidc_app.claims = {ROLE_CLAIM: {"reader": {"org-1": "org.example"}}}
    assert complete(oidc_app).headers["location"].endswith("error=access_denied")


def test_denial_revokes_previous_local_session_and_clears_both_cookies(oidc_app):
    state = oidc_app
    complete(state)
    previous = state.client.cookies.get("__Host-devfeed_admin_session")
    state.claims[ROLE_CLAIM] = {}
    response = complete(state)
    assert response.headers["location"].endswith("error=access_denied")
    assert state.store.get(auth.key("session", previous)) is None
    assert state.client.cookies.get("__Host-devfeed_admin_session") is None
    assert state.client.cookies.get("__Host-devfeed_admin_state") is None
    assert len(response.headers.get_list("set-cookie")) == 2
    assert all("Max-Age=0" in value for value in response.headers.get_list("set-cookie"))
    state.client.cookies.set("__Host-devfeed_admin_session", previous)
    assert state.client.get("/v1/admin/auth/me").status_code == 401


def test_role_removed_then_restored_can_sign_in_again_in_same_browser(oidc_app):
    state = oidc_app
    complete(state)
    assert (
        state.client.post("/v1/admin/auth/logout", headers=logout_headers(state)).status_code == 204
    )
    state.claims[ROLE_CLAIM] = {}
    assert complete(state).headers["location"].endswith("error=access_denied")
    assert state.client.get("/v1/admin/auth/me").status_code == 401
    flow = begin(state, reauthenticate=True)
    assert state.params["prompt"] == ["login"]
    assert state.params["max_age"] == ["0"]
    assert state.params["code_challenge_method"] == ["S256"]
    state.claims[ROLE_CLAIM] = {"superuser": {"org-1": "org.example"}}
    assert complete(state, flow).headers["location"] == ORIGIN + "/"
    assert state.client.get("/v1/admin/auth/me").json()["roles"] == ["superuser"]


@pytest.mark.parametrize("auth_time", [None, True, "123", 1, int(time.time()) + 600])
def test_reauthentication_rejects_stale_or_invalid_signed_auth_time(oidc_app, auth_time):
    oidc_app.claims["auth_time"] = auth_time
    # UserInfo cannot repair a missing/stale signed authentication claim.
    oidc_app.info["auth_time"] = int(time.time())
    flow = begin(oidc_app, reauthenticate=True)
    assert complete(oidc_app, flow).headers["location"].endswith("error=login_failed")
    assert not any(":session:" in key for key in oidc_app.store.values)


def test_reauthentication_requires_auth_time_but_normal_sso_does_not(oidc_app):
    oidc_app.omit_claims.add("auth_time")
    assert complete(oidc_app).headers["location"] == ORIGIN + "/"
    assert "prompt" not in oidc_app.params and "max_age" not in oidc_app.params
    flow = begin(oidc_app, reauthenticate=True)
    assert complete(oidc_app, flow).headers["location"].endswith("error=login_failed")
    assert oidc_app.client.get("/v1/admin/auth/me").status_code == 401


def test_fresh_authentication_does_not_bypass_required_role(oidc_app):
    oidc_app.claims[ROLE_CLAIM] = {}
    flow = begin(oidc_app, reauthenticate=True)
    assert complete(oidc_app, flow).headers["location"].endswith("error=access_denied")
    assert oidc_app.client.get("/v1/admin/auth/me").status_code == 401


def test_unbound_or_replayed_callback_cannot_log_out_an_existing_admin(oidc_app):
    flow = begin(oidc_app)
    complete(oidc_app, flow)
    previous = oidc_app.client.cookies.get("__Host-devfeed_admin_session")
    for invalid_state in (secrets.token_urlsafe(32), flow):
        response = complete(oidc_app, invalid_state)
        assert response.headers["location"].endswith("error=login_failed")
        assert not any(
            "admin_session=" in value for value in response.headers.get_list("set-cookie")
        )
        assert oidc_app.client.cookies.get("__Host-devfeed_admin_session") == previous
        assert oidc_app.client.get("/v1/admin/auth/me").status_code == 200


def test_validated_provider_cancellation_clears_previous_local_identity(oidc_app):
    complete(oidc_app)
    previous = oidc_app.client.cookies.get("__Host-devfeed_admin_session")
    response = complete(oidc_app, extra={"error": "access_denied"})
    assert response.headers["location"].endswith("error=login_failed")
    assert oidc_app.store.get(auth.key("session", previous)) is None
    assert oidc_app.client.get("/v1/admin/auth/me").status_code == 401


def test_callback_revocation_failure_cannot_mint_a_replacement_session(oidc_app, monkeypatch):
    state = oidc_app
    complete(state)
    previous = state.client.cookies.get("__Host-devfeed_admin_session")
    previous_key = auth.key("session", previous)
    original_delete = state.store.delete

    def unavailable(key):
        if key == previous_key:
            raise ConnectionError()
        original_delete(key)

    monkeypatch.setattr(state.store, "delete", unavailable)
    response = complete(state)
    assert response.headers["location"].endswith("error=login_failed")
    assert state.client.cookies.get("__Host-devfeed_admin_session") is None
    assert [key for key in state.store.values if ":session:" in key] == [previous_key]
    assert sum(request.url.path == "/token" for request in state.requests) == 1


def test_new_subject_with_superuser_role_needs_no_allowlist(oidc_app):
    oidc_app.claims["sub"] = "new-admin-not-in-a-local-list"
    oidc_app.info["sub"] = "new-admin-not-in-a-local-list"
    assert complete(oidc_app).headers["location"] == ORIGIN + "/"
    assert oidc_app.client.get("/v1/admin/auth/me").json()["roles"] == ["superuser"]


@pytest.mark.parametrize(
    "roles",
    [
        None,
        {},
        "superuser",
        ["superuser"],
        {"superuser": True},
        {"superuser": {"another-org": "other.example"}},
    ],
)
def test_role_failures_do_not_create_sessions(oidc_app, roles):
    oidc_app.claims[ROLE_CLAIM] = roles
    assert complete(oidc_app).headers["location"].endswith("error=access_denied")
    assert not any(":session:" in key for key in oidc_app.store.values)
    assert oidc_app.client.get("/v1/admin/overview").status_code == 401


def test_userinfo_can_supply_subject_matched_role_grants(oidc_app):
    oidc_app.omit_claims.add(ROLE_CLAIM)
    oidc_app.info[ROLE_CLAIM] = {"superuser": {"org-1": "org.example"}}
    assert complete(oidc_app).headers["location"] == ORIGIN + "/"


def test_roles_absent_from_both_identity_sources_are_denied(oidc_app):
    oidc_app.omit_claims.add(ROLE_CLAIM)
    assert complete(oidc_app).headers["location"].endswith("error=access_denied")


def test_userinfo_cannot_override_a_missing_role_in_id_token(oidc_app):
    oidc_app.claims[ROLE_CLAIM] = {}
    oidc_app.info[ROLE_CLAIM] = {"superuser": {"org-1": "org.example"}}
    assert complete(oidc_app).headers["location"].endswith("error=access_denied")


def test_userinfo_cannot_retain_a_role_it_explicitly_denies(oidc_app):
    oidc_app.info[ROLE_CLAIM] = {}
    assert complete(oidc_app).headers["location"].endswith("error=access_denied")


def test_every_protected_request_checks_session_role(oidc_app):
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_admin_session")
    key = auth.key("session", token)
    raw, expiry = oidc_app.store.values[key]
    record = json.loads(raw)
    record["roles"] = ["reader"]
    oidc_app.store.values[key] = (json.dumps(record).encode(), expiry)
    for path in ("/v1/admin/auth/me", "/v1/admin/overview", "/v1/admin/ingestion/jobs"):
        assert oidc_app.client.get(path).status_code == 403


def test_old_allowlist_sessions_cannot_skip_roles(oidc_app):
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_admin_session")
    key = auth.key("session", token)
    raw, expiry = oidc_app.store.values[key]
    record = json.loads(raw)
    del record["roles"]
    oidc_app.store.values[key] = (json.dumps(record).encode(), expiry)
    assert oidc_app.client.get("/v1/admin/auth/me").status_code == 401


def test_flat_generic_roles_can_authorize_a_login(oidc_app):
    oidc_app.settings.oidc_roles_claim = "app_roles"
    oidc_app.settings.oidc_roles_format = "string_list"
    oidc_app.settings.oidc_role_scope_template = "roles"
    oidc_app.claims["app_roles"] = ["superuser"]
    assert complete(oidc_app).headers["location"] == ORIGIN + "/"
    assert "roles" in oidc_app.params["scope"][0].split()


def test_userinfo_cannot_switch_identity(oidc_app):
    oidc_app.info = {"sub": "someone-else"}
    assert complete(oidc_app).headers["location"].endswith("error=login_failed")


def test_callback_is_browser_bound_and_single_use(oidc_app):
    flow = begin(oidc_app)
    browser = oidc_app.client.cookies.get("__Host-devfeed_admin_state")
    oidc_app.client.cookies.clear()
    assert complete(oidc_app, flow).headers["location"].endswith("error=login_failed")
    assert oidc_app.store.get(auth.key("flow", flow)) is not None
    oidc_app.client.cookies.set("__Host-devfeed_admin_state", browser)
    assert complete(oidc_app, flow).headers["location"] == ORIGIN + "/"
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
    assert oidc_app.client.get("/v1/admin/auth/login").status_code == 503


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}, {"Origin": ORIGIN}])
def test_csrf_protects_logout_and_taxonomy_writes(oidc_app, headers):
    complete(oidc_app)
    for path in ("/v1/admin/auth/logout", "/v1/admin/tags", "/v1/admin/topics"):
        assert oidc_app.client.post(path, json={}, headers=headers).status_code == 403

    assert (
        oidc_app.client.delete(
            f"/v1/admin/topic-proposals/{uuid.uuid4()}", headers=headers
        ).status_code
        == 403
    )


def test_cached_private_endpoints_still_require_authentication(oidc_app):
    for path in ("/v1/admin/overview", "/v1/admin/ingestion/jobs", "/v1/admin/topics"):
        response = oidc_app.client.get(path)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert "x-cache" not in response.headers


def test_policy_changes_and_expiry_revoke_access(oidc_app):
    complete(oidc_app)
    oidc_app.settings.admin_required_role = "different-role"
    assert oidc_app.client.get("/v1/admin/auth/me").status_code == 401


def test_redis_failure_is_not_an_authentication_bypass(oidc_app, monkeypatch):
    complete(oidc_app)
    monkeypatch.setattr(oidc_app.store, "get", lambda key: (_ for _ in ()).throw(ConnectionError()))
    assert oidc_app.client.get("/v1/admin/auth/me").status_code == 503


def test_unconfigured_admin_is_closed_and_public_api_is_independent(oidc_app):
    from devfeed_api.main import create_app as public_app

    oidc_app.settings.oidc_organization_id = None
    assert oidc_app.client.get("/v1/admin/auth/config").json() == {"enabled": False}
    assert oidc_app.client.get("/v1/admin/overview").status_code == 503
    schema = public_app().openapi()
    assert not any("/admin" in path or "/ingestion" in path for path in schema["paths"])
    assert set(schema["paths"]["/v1/topics"]) == {"get"}
    assert set(schema["paths"]["/v1/tags"]) == {"get"}


def test_random_cookie_and_state_are_rejected(oidc_app):
    oidc_app.client.cookies.set("__Host-devfeed_admin_session", secrets.token_urlsafe(32))
    assert oidc_app.client.get("/v1/admin/auth/me").status_code == 401
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
    assert complete(oidc_app).headers["location"] == ORIGIN + "/"
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
    assert complete(oidc_app).headers["location"] == ORIGIN + "/"
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
    assert oidc_app.client.get("/v1/admin/auth/login").status_code == 503


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
    token = oidc_app.client.cookies.get("__Host-devfeed_admin_session")
    key = auth.key("session", token)
    raw, expiry = oidc_app.store.values[key]
    record = json.loads(raw)
    record["expires_at"] = 1
    oidc_app.store.values[key] = (json.dumps(record).encode(), expiry)
    assert oidc_app.client.get("/v1/admin/auth/me").status_code == 401


def test_authenticated_overview_returns_database_counts(oidc_app):
    complete(oidc_app)
    rows = iter([(12, 7, 2), (5, 1)])
    session = SimpleNamespace(
        execute=lambda query: SimpleNamespace(one=lambda: next(rows)),
        scalar=lambda query: 4,
    )
    oidc_app.client.app.dependency_overrides[get_session] = lambda: session
    response = oidc_app.client.get("/v1/admin/overview")
    assert response.json() == {
        "articles": 12,
        "articles_pending_review": 7,
        "articles_published": 2,
        "sources": 5,
        "sources_pending_review": 1,
        "topics": 4,
    }
    assert response.headers["cache-control"] == "no-store"


def test_secure_cookie_and_origin_configuration_is_explicit():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, admin_base_url="http://localhost:3000")
    settings = Settings(
        _env_file=None, admin_base_url="http://localhost:3000", admin_cookie_secure=False
    )
    assert oidc.cookie_name(settings, "session") == "devfeed_admin_session"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, oidc_issuer_url="http://identity.example")
