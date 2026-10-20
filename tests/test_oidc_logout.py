"""Signed provider requests, real Redis scripts, and callback/renewal ordering."""

import json
import os
import time
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from devfeed_http import oidc, oidc_logout
from redis import Redis
from redis.exceptions import ConnectionError
from test_admin_auth import complete as admin_complete
from test_admin_auth import oidc_app as admin_app  # noqa: F401
from test_partner_portal_auth import complete as partner_complete
from test_partner_portal_auth import oidc_app as partner_app  # noqa: F401
from test_user_auth import complete as reader_complete
from test_user_auth import oidc_app as reader_app  # noqa: F401


@pytest.fixture
def signed(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update(kid="logout-key", alg="RS256", use="sig")
    monkeypatch.setattr(oidc, "request_json", lambda *args, **kwargs: {"keys": [public]})
    settings = SimpleNamespace(oidc_issuer_url="https://issuer.test", oidc_client_id="reader")
    return SimpleNamespace(
        key=key, settings=settings, metadata={"jwks_uri": "https://issuer.test/keys"}
    )


def claims(**changes):
    return {
        "iss": "https://issuer.test",
        "aud": "reader",
        "iat": int(time.time()),
        "jti": str(uuid.uuid4()),
        "events": {oidc_logout.EVENT: {}},
        "sid": "provider-session",
        **changes,
    }


def sign(state, payload):
    return jwt.encode(payload, state.key, algorithm="RS256", headers={"kid": "logout-key"})


@pytest.mark.parametrize(
    "change",
    [
        {"iss": "https://other.test"},
        {"aud": "admin"},
        {"aud": ["reader", "reader"]},
        {"iat": int(time.time()) - 1000},
        {"iat": lambda: int(time.time()) + 120},
        {"iat": True},
        {"iat": "123"},
        {"exp": 1},
        {"exp": True},
        {"exp": "123"},
        {"jti": ""},
        {"jti": []},
        {"nonce": "login-nonce"},
        {"events": {}},
        {"events": {oidc_logout.EVENT: [], "other-event": {}}},
        {"sid": ""},
        {"sid": None},
        {"sub": ""},
        {"sub": []},
    ],
)
def test_invalid_logout_claims(signed, change):
    change = {key: value() if callable(value) else value for key, value in change.items()}
    with pytest.raises(oidc.OIDCError, match="Invalid logout token"):
        oidc_logout.validate(signed.settings, signed.metadata, sign(signed, claims(**change)))


@pytest.mark.parametrize("missing", ["iss", "aud", "iat", "jti", "events", "sid"])
def test_required_logout_claims(signed, missing):
    value = claims()
    value.pop(missing)
    with pytest.raises(oidc.OIDCError):
        oidc_logout.validate(signed.settings, signed.metadata, sign(signed, value))


@pytest.mark.parametrize("target", [{"sid": "s"}, {"sub": "u"}, {"sid": "s", "sub": "u"}])
def test_valid_logout_claims_without_optional_expiration(signed, target):
    value = claims()
    value.pop("sid")
    value.update(target)
    assert oidc_logout.validate(signed.settings, signed.metadata, sign(signed, value)) == value


def test_id_token_cannot_be_used_as_logout_token(signed):
    value = claims(nonce="login", exp=int(time.time()) + 300)
    with pytest.raises(oidc.OIDCError):
        oidc_logout.validate(signed.settings, signed.metadata, sign(signed, value))


@pytest.mark.parametrize(
    "header",
    [
        {"jku": "https://attacker.test/keys"},
        {"jwk": {}},
        {"x5u": "https://attacker.test"},
        {"crit": ["custom"]},
        {"kid": "other-key"},
        {"typ": "at+jwt"},
    ],
)
def test_untrusted_keys_and_token_types_are_rejected(signed, header):
    token = jwt.encode(
        claims(), signed.key, algorithm="RS256", headers={"kid": "logout-key", **header}
    )
    with pytest.raises(oidc.OIDCError):
        oidc_logout.validate(signed.settings, signed.metadata, token)


def test_invalid_signature_and_symmetric_algorithm(signed):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for token in [
        jwt.encode(claims(), other, algorithm="RS256", headers={"kid": "logout-key"}),
        jwt.encode(claims(), "s" * 32, algorithm="HS256"),
        "not.a.jwt",
    ]:
        with pytest.raises(oidc.OIDCError):
            oidc_logout.validate(signed.settings, signed.metadata, token)


class IsolatedRedis:
    """Real Redis behind a unique prefix; never flush another test's data."""

    def __init__(self, redis):
        self.redis = redis
        self.prefix = f"logout-test:{uuid.uuid4()}:"

    def get(self, key):
        return self.redis.get(self.prefix + key)

    def set(self, key, value, ex):
        return self.redis.set(self.prefix + key, value, ex=ex)

    def getdel(self, key):
        return self.redis.getdel(self.prefix + key)

    def delete(self, key):
        return self.redis.delete(self.prefix + key)

    def eval(self, script, number, *args):
        return self.redis.eval(
            script, number, *[self.prefix + key for key in args[:number]], *args[number:]
        )

    def close(self):
        for key in self.redis.scan_iter(self.prefix + "*"):
            self.redis.delete(key)


@pytest.fixture
def logout_redis():
    value = os.environ.get("DEVFEED_TEST_REDIS_URL")
    if not value:
        pytest.skip(
            "Set disposable DEVFEED_TEST_REDIS_URL (database 15) for logout integration tests"
        )
    assert urlsplit(value).path == "/15"
    store = IsolatedRedis(Redis.from_url(value))
    try:
        yield store
    finally:
        store.close()


@pytest.fixture(params=["reader", "admin", "partner"])
def application(request, monkeypatch, logout_redis):
    state = request.getfixturevalue(request.param + "_app")
    module_name = "user" if request.param == "reader" else request.param
    module = __import__(f"devfeed_{module_name}_api.auth", fromlist=["auth"])
    monkeypatch.setattr(module, "get_redis", lambda: logout_redis)
    state.store = logout_redis
    state.service = module_name
    state.module = module
    state.complete = {
        "reader": reader_complete,
        "admin": admin_complete,
        "partner": partner_complete,
    }[request.param]
    state.claims["sid"] = "provider-session"
    state.metadata["end_session_endpoint"] = "https://identity.example/end-session"
    return state


def login(state):
    response = state.complete(state)
    assert response.status_code == 302
    assert "error=" not in response.headers["location"]
    identity = state.client.get(f"/v1/{state.service}/auth/me")
    assert identity.status_code == 200
    return identity.json()


def event(state, **changes):
    value = claims(iss="https://identity.example", aud=state.settings.oidc_client_id, **changes)
    return jwt.encode(value, state.key, algorithm="RS256", headers={"kid": "key-1"})


def deliver(state, token):
    return state.client.post(
        f"/v1/{state.service}/auth/backchannel-logout", data={"logout_token": token}
    )


def authenticated(state):
    response = state.client.get(f"/v1/{state.service}/auth/me")
    return response.status_code == 200 and response.json() is not None


def test_server_to_server_revokes_session_and_duplicates_succeed(application):
    state = application
    login(state)
    token = event(state)
    # Provider request has neither browser credentials nor an Origin/CSRF token.
    cookies = list(state.client.cookies.jar)
    state.client.cookies.clear()
    assert deliver(state, token).status_code == 200
    assert deliver(state, token).status_code == 200
    for cookie in cookies:
        state.client.cookies.jar.set_cookie(cookie)
    assert not authenticated(state)
    assert state.settings.oidc_client_id.encode() not in deliver(state, token).content


@pytest.mark.parametrize(
    "change",
    [
        {"aud": "other-client"},
        {"sub": "other-subject"},
        {"sid": "other-session"},
    ],
)
def test_other_clients_sessions_and_mismatched_subjects_are_untouched(application, change):
    state = application
    login(state)
    value = claims(iss="https://identity.example", aud=state.settings.oidc_client_id)
    value.update(change)
    token = jwt.encode(value, state.key, algorithm="RS256", headers={"kid": "key-1"})
    expected = 400 if "aud" in change else 200
    assert deliver(state, token).status_code == expected
    assert authenticated(state)


def test_unknown_logout_blocks_late_callback_for_same_provider_session(application):
    state = application
    assert deliver(state, event(state)).status_code == 200
    response = state.complete(state)
    assert "error=login_failed" in response.headers["location"]
    assert not authenticated(state)


@pytest.mark.parametrize(
    "body",
    ["logout_token=x&logout_token=y", "other=x", "logout_token=", "logout_token=" + "x" * 17000],
)
def test_malformed_ambiguous_and_oversized_requests(application, body):
    response = application.client.post(
        f"/v1/{application.service}/auth/backchannel-logout",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code in {400, 413}
    assert "x" * 100 not in response.text


def test_dependency_outage_is_retryable(application, monkeypatch):
    state = application
    login(state)

    def unavailable(*args, **kwargs):
        raise ConnectionError("Redis unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(state.store, "eval", unavailable)
        assert deliver(state, event(state)).status_code == 503
    assert authenticated(state)
    token = event(state)
    assert deliver(state, token).status_code == 200
    assert not authenticated(state)


def test_sso_flow_requires_csrf_preserves_local_logout_and_correlates_completion(application):
    state = application
    user = login(state)
    base = (
        state.settings.base_url
        if state.service == "user"
        else getattr(state.settings, state.service + "_base_url")
    )
    endpoint = f"/v1/{state.service}/auth/sso/logout"
    assert state.client.post(endpoint).status_code == 403
    response = state.client.post(
        endpoint, headers={"Origin": base, "X-CSRF-Token": user["csrf_token"]}
    )
    assert response.status_code == 200
    url = urlsplit(response.json()["url"])
    assert url.netloc == "identity.example"
    params = parse_qs(url.query)
    assert params["client_id"] == [state.settings.oidc_client_id]
    assert params["post_logout_redirect_uri"] == [
        base + f"/api/v1/{state.service}/auth/sso/callback"
    ]
    assert not authenticated(state)
    callback = f"/v1/{state.service}/auth/sso/callback?" + urlencode({"state": params["state"][0]})
    cookie_name = state.module.oidc.cookie_name(state.settings, "logout_state")
    browser = state.client.cookies.get(cookie_name)
    state.client.cookies.clear()
    assert state.client.get(callback, follow_redirects=False).status_code == 400
    state.client.cookies.set(cookie_name, browser)
    done = state.client.get(callback, follow_redirects=False)
    assert done.status_code == 302
    assert done.headers["location"] == base + "/login?logout=sso"
    assert state.client.get(callback, follow_redirects=False).status_code == 400


def test_legacy_sessions_without_correlation_require_login(application):
    state = application
    login(state)
    cookie = state.module.oidc.cookie_name(state.settings, "session")
    key = state.module.key("session", state.client.cookies.get(cookie))
    record = json.loads(state.store.get(key))
    record.pop("provider_session")
    state.store.set(key, json.dumps(record), ex=300)
    assert not authenticated(state)


def test_subject_cutoffs_monotonic_and_isolated_from_other_client_and_service(logout_redis, signed):
    settings = signed.settings
    record = {
        "subject": "user",
        "provider_session": {
            "issuer": settings.oidc_issuer_url,
            "client_id": settings.oidc_client_id,
            "subject": "user",
            "sid": "sid",
            "issued_at": int(time.time()) - 50,
        },
    }
    value = claims(sub="user", iat=int(time.time()) - 10)
    value.pop("sid")
    oidc_logout.revoke(logout_redis, "user", settings, value)
    older = {**value, "iat": value["iat"] - 100, "jti": "older"}
    oidc_logout.revoke(logout_redis, "user", settings, older)
    assert not oidc_logout.active(logout_redis, "user", settings, record)
    assert oidc_logout.active(logout_redis, "admin", settings, record)
    record["provider_session"]["issued_at"] = value["iat"] + 1
    assert oidc_logout.active(logout_redis, "user", settings, record)
    other = SimpleNamespace(oidc_issuer_url=settings.oidc_issuer_url, oidc_client_id="other-client")
    record["provider_session"]["client_id"] = "other-client"
    assert oidc_logout.active(logout_redis, "user", other, record)


def test_logout_between_session_write_and_registration_check_prevents_creation(
    logout_redis, signed, monkeypatch
):
    settings = signed.settings
    value = claims()
    record = {
        "subject": "user",
        "provider_session": {
            "issuer": settings.oidc_issuer_url,
            "client_id": settings.oidc_client_id,
            "subject": "user",
            "sid": value["sid"],
            "issued_at": value["iat"] - 1,
        },
    }
    original = logout_redis.set

    def interleaved(key, value_to_store, ex):
        result = original(key, value_to_store, ex)
        oidc_logout.revoke(logout_redis, "user", settings, value)
        return result

    monkeypatch.setattr(logout_redis, "set", interleaved)
    with pytest.raises(oidc.OIDCError):
        oidc_logout.save(logout_redis, "user", settings, "session-key", record, 300)
    assert logout_redis.get("session-key") is None


def test_reused_jti_with_different_target_does_not_revoke_another_session(logout_redis, signed):
    value = claims()
    oidc_logout.revoke(logout_redis, "user", signed.settings, value)
    with pytest.raises(oidc.OIDCError, match="Conflicting"):
        oidc_logout.revoke(logout_redis, "user", signed.settings, {**value, "sid": "other"})
    prefix = oidc_logout.scope("user", signed.settings.oidc_issuer_url, "reader")
    assert logout_redis.get(oidc_logout.marker(prefix, "other", None)) is None
    assert (
        logout_redis.redis.ttl(logout_redis.prefix + oidc_logout.marker(prefix, value["sid"], None))
        > 90 * 86400
    )


def test_sid_only_event_does_not_revoke_uncorrelated_subject_session(logout_redis, signed):
    value = claims()
    oidc_logout.revoke(logout_redis, "user", signed.settings, value)
    record = {
        "subject": "user",
        "provider_session": {
            "issuer": signed.settings.oidc_issuer_url,
            "client_id": "reader",
            "subject": "user",
            "sid": None,
            "issued_at": value["iat"] - 10,
        },
    }
    assert oidc_logout.active(logout_redis, "user", signed.settings, record)
    subject_event = {**value, "sub": "user", "jti": "subject-only"}
    subject_event.pop("sid")
    oidc_logout.revoke(logout_redis, "user", signed.settings, subject_event)
    assert not oidc_logout.active(logout_redis, "user", signed.settings, record)


def test_logout_during_idle_renewal_cannot_restore_reader_access(
    request, logout_redis, monkeypatch
):
    from devfeed_user_api import auth

    state = request.getfixturevalue("reader_app")
    monkeypatch.setattr(auth, "get_redis", lambda: logout_redis)
    state.claims["sid"] = "provider-session"
    assert reader_complete(state).status_code == 302
    token = state.client.cookies.get(auth.oidc.cookie_name(state.settings, "session"))
    key = auth.key("session", token)
    record = json.loads(logout_redis.get(key))
    record["renewed_at"] = int(time.time()) - 4000
    logout_redis.set(key, json.dumps(record), ex=300)
    original = logout_redis.eval

    def interleaved(script, number, *args):
        if script == auth.RENEW_SESSION:
            oidc_logout.revoke(logout_redis, "user", state.settings, claims())
        return original(script, number, *args)

    monkeypatch.setattr(logout_redis, "eval", interleaved)
    response = state.client.get("/v1/user/auth/me")
    assert response.status_code == 200 and response.json() is None
    assert not oidc_logout.active(logout_redis, "user", state.settings, record)


def test_duplicate_jwt_json_members_are_rejected(signed):
    value = json.dumps(claims())
    value = value[:-1] + ', "sid": "ambiguous"}'
    token = jwt.api_jws.encode(
        value.encode(), signed.key, algorithm="RS256", headers={"kid": "logout-key"}
    )
    with pytest.raises(oidc.OIDCError):
        oidc_logout.validate(signed.settings, signed.metadata, token)


def test_provider_unavailable_returns_retryable_failure(application, monkeypatch):
    def unavailable(*args, **kwargs):
        raise oidc.OIDCDependencyError("Unavailable")

    monkeypatch.setattr(oidc, "request_json", unavailable)
    assert deliver(application, event(application)).status_code == 503


def test_browser_logout_leaves_independent_mcp_credentials_unchanged(application):
    state = application
    login(state)
    grant_key = "devfeed:mcp:grant:independent-test-grant"
    value = json.dumps({"subject": "independent-user", "expires_at": int(time.time()) + 3600})
    state.store.set(grant_key, value, ex=3600)
    assert deliver(state, event(state)).status_code == 200
    assert state.store.get(grant_key) == value.encode()


def test_reader_mcp_policy_key_keeps_its_existing_version(request):
    import hashlib

    from devfeed_user_api import oidc as reader_oidc

    settings = request.getfixturevalue("reader_app").settings
    values = {
        key: value
        for key, value in settings.model_dump(mode="json").items()
        if key.startswith(("oidc_", "session_")) or key in {"base_url", "cookie_secure"}
    }
    values["authorization_policy_version"] = "user-organization-v1"
    expected = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    assert reader_oidc.policy_key(settings) == expected


def test_pathological_json_and_numeric_dates_are_rejected(signed):
    for payload in [
        b'{"nested":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}",
        json.dumps(claims(iat=float("inf"))).encode(),
    ]:
        token = jwt.api_jws.encode(
            payload, signed.key, algorithm="RS256", headers={"kid": "logout-key"}
        )
        with pytest.raises(oidc.OIDCError):
            oidc_logout.validate(signed.settings, signed.metadata, token)


def test_reader_sso_completion_uses_registered_trusted_alias(request):
    from fastapi.testclient import TestClient

    state = request.getfixturevalue("reader_app")
    assert reader_complete(state).status_code == 302
    alias = "https://reader-alias.example"
    state.settings.allowed_origins = [alias]
    state.metadata["end_session_endpoint"] = "https://identity.example/end-session"
    cookie = "__Host-devfeed_user_session"
    token = state.client.cookies.get(cookie)
    identity = state.client.get("/v1/user/auth/me").json()
    with TestClient(state.client.app, base_url=alias) as client:
        client.cookies.set(cookie, token)
        response = client.post(
            "/v1/user/auth/sso/logout",
            headers={"Origin": alias, "X-CSRF-Token": identity["csrf_token"]},
        )
        assert response.status_code == 200
        params = parse_qs(urlsplit(response.json()["url"]).query)
        assert params["post_logout_redirect_uri"] == [alias + "/api/v1/user/auth/sso/callback"]
        done = client.get(
            "/v1/user/auth/sso/callback",
            params={"state": params["state"][0]},
            follow_redirects=False,
        )
        assert done.status_code == 302
        assert done.headers["location"] == alias + "/login?logout=sso"


def test_sso_logout_still_works_after_provider_revocation(application):
    state = application
    user = login(state)
    assert deliver(state, event(state)).status_code == 200
    assert not authenticated(state)
    base = (
        state.settings.base_url
        if state.service == "user"
        else getattr(state.settings, state.service + "_base_url")
    )
    response = state.client.post(
        f"/v1/{state.service}/auth/sso/logout",
        headers={"Origin": base, "X-CSRF-Token": user["csrf_token"]},
    )
    assert response.status_code == 200
    assert urlsplit(response.json()["url"]).netloc == "identity.example"
