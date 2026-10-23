"""Session corruption, provider revocation and callback expiry cannot restore admin access."""

import json
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from devfeed_admin_api import auth
from devfeed_admin_api.config import Settings
from devfeed_http import oidc_logout
from redis.exceptions import ConnectionError

from tests.admin.auth.test_auth import ORIGIN, begin, complete
from tests.admin.auth.test_auth import oidc_app as oidc_app


@pytest.fixture
def admin_session(oidc_app):
    assert complete(oidc_app).headers["location"] == ORIGIN + "/start"
    token = oidc_app.client.cookies.get("__Host-devfeed_admin_session")
    key = auth.key("session", token)
    record = json.loads(oidc_app.store.get(key))
    return SimpleNamespace(state=oidc_app, token=token, key=key, record=record)


@pytest.mark.parametrize("raw", ["{", "[]", "null"])
def test_malformed_retained_session_is_unauthenticated_without_echoing_or_replacing_it(
    admin_session, raw
):
    value = admin_session
    value.state.store.set(value.key, raw, ex=300)
    response = value.state.client.get("/v1/admin/auth/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "Admin session expired"}
    assert value.state.store.get(value.key) == raw.encode()
    assert value.state.client.cookies.get("__Host-devfeed_admin_session") == value.token
    assert "set-cookie" not in response.headers


def test_provider_logout_marker_denies_the_existing_session_but_allows_local_sign_out(
    admin_session,
):
    value = admin_session
    context = value.record["provider_session"]
    prefix = oidc_logout.scope("admin", context["issuer"], context["client_id"])
    value.state.store.set(
        oidc_logout.marker(prefix, None, context["subject"]), str(context["issued_at"]), ex=300
    )
    response = value.state.client.get("/v1/admin/auth/me")
    assert response.status_code == 401 and response.json() == {"detail": "Admin session expired"}
    assert value.state.store.get(value.key) is not None
    response = value.state.client.post(
        "/v1/admin/auth/logout",
        headers={"Origin": ORIGIN, "X-CSRF-Token": value.record["csrf_token"]},
    )
    assert response.status_code == 204 and value.state.store.get(value.key) is None


def test_provider_revocation_lookup_outage_is_retryable_without_treating_the_user_as_authorized(
    admin_session, monkeypatch
):
    value = admin_session
    read = value.state.store.get

    def unavailable(key):
        if ":provider-logout:" in key:
            raise ConnectionError("redis://private-revocation-endpoint/15")
        return read(key)

    monkeypatch.setattr(value.state.store, "get", unavailable)
    response = value.state.client.get("/v1/admin/auth/me")
    assert response.status_code == 503
    assert response.json() == {"detail": "Admin sessions temporarily unavailable"}
    assert "private" not in response.text and "set-cookie" not in response.headers
    assert value.state.store.get(value.key) is not None


@pytest.mark.parametrize("raw", ["{", "null", '{"private":"invalid-csrf-record"}'])
def test_logout_keeps_a_corrupt_session_for_retry_instead_of_claiming_successful_revocation(
    admin_session, raw
):
    value = admin_session
    value.state.store.set(value.key, raw, ex=300)
    response = value.state.client.post(
        "/v1/admin/auth/logout",
        headers={"Origin": ORIGIN, "X-CSRF-Token": value.record["csrf_token"]},
    )
    assert response.status_code == 401 and response.json() == {"detail": "Invalid admin session"}
    assert "private" not in response.text and "set-cookie" not in response.headers
    assert value.state.store.get(value.key) == raw.encode()


def test_logout_without_a_configured_admin_origin_refuses_to_change_browser_or_store(
    oidc_app, monkeypatch
):
    monkeypatch.setattr(auth, "get_settings", lambda: Settings(_env_file=None))
    redis = Mock(side_effect=AssertionError("Unconfigured logout reached Redis"))
    monkeypatch.setattr(auth, "get_redis", redis)
    response = oidc_app.client.post("/v1/admin/auth/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 503
    assert response.json() == {"detail": "Admin origin is not configured"}
    assert "set-cookie" not in response.headers
    redis.assert_not_called()


@pytest.mark.parametrize("expired", [False, True])
def test_recovery_login_rejects_expired_context_or_preserves_retry_after_provider_outage(
    oidc_app, monkeypatch, expired
):
    response = complete(oidc_app, extra={"error": "access_denied"})
    retry = parse_qs(urlsplit(response.headers["location"]).query)["retry"][0]
    key = auth.key("retry", retry)
    if expired:
        raw, _ = oidc_app.store.values[key]
        oidc_app.store.values[key] = (raw, 1)
    else:
        monkeypatch.setattr(
            auth.oidc,
            "discovery",
            Mock(side_effect=auth.oidc.OIDCError("private identity dependency")),
        )
    response = oidc_app.client.get(
        "/v1/admin/auth/login", params={"retry": retry}, follow_redirects=False
    )
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    query = parse_qs(location.query)
    assert location.netloc == "admin.example" and location.path == "/login"
    assert query["error"] == ["login_expired" if expired else "login_unavailable"]
    assert ("retry" in query) is not expired
    assert "private" not in response.headers["location"]
    assert not any(":session:" in key for key in oidc_app.store.values)


def test_identity_expiring_between_token_validation_and_session_save_cannot_create_a_session(
    oidc_app, monkeypatch
):
    flow = begin(oidc_app)
    identify = auth.oidc.identity
    save = Mock(wraps=oidc_logout.save)
    clock = SimpleNamespace(now=0)

    def expires_after_validation(*args, **kwargs):
        identity = identify(*args, **kwargs)
        clock.now = identity["expires_at"]
        return identity

    monkeypatch.setattr(auth.oidc, "identity", expires_after_validation)
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: clock.now))
    monkeypatch.setattr(oidc_logout, "save", save)
    response = complete(oidc_app, flow)
    assert response.status_code == 302
    assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == ["login_failed"]
    assert oidc_app.client.cookies.get("__Host-devfeed_admin_session") is None
    save.assert_not_called()
