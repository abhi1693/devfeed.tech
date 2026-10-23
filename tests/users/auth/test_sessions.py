"""Reader session probes fail closed across dependency and later-read boundaries.

The signed mock OIDC provider creates real valid session histories. Redis reads
and provider-revocation status are explicit external boundaries; no Lua renewal
atomicity is simulated or claimed by these cases.
"""

import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_user_api import auth
from redis.exceptions import ConnectionError

from tests.users.auth.test_auth import ORIGIN, complete
from tests.users.auth.test_auth import oidc_app as oidc_app


def test_unconfigured_identity_service_is_unavailable_instead_of_an_anonymous_session_probe(
    oidc_app, monkeypatch
):
    oidc_app.settings.oidc_client_id = None
    redis = Mock()
    monkeypatch.setattr(auth, "get_redis", redis)
    response = oidc_app.client.get("/v1/user/auth/me")
    assert response.status_code == 503
    assert response.json() == {"detail": "User authentication is not configured"}
    redis.assert_not_called()
    assert not response.cookies


@pytest.mark.parametrize("operation", ["read", "write"])
def test_activity_marker_failure_is_best_effort_and_does_not_reject_a_valid_session(
    oidc_app, monkeypatch, operation, caplog
):
    complete(oidc_app)
    store = oidc_app.store
    original = getattr(store, "get" if operation == "read" else "set")

    def marker_failure(key, *args, **kwargs):
        if key.startswith("devfeed:user:activity:"):
            raise ConnectionError("private-store-endpoint")
        return original(key, *args, **kwargs)

    monkeypatch.setattr(store, "get" if operation == "read" else "set", marker_failure)
    durable_activity = Mock()
    monkeypatch.setattr(auth, "touch_user_activity", durable_activity)
    response = oidc_app.client.get("/v1/user/auth/me")
    assert response.status_code == 200 and response.json()["subject"] == "user-1"
    assert "user_activity_marker_unavailable" in caplog.messages
    assert "private-store-endpoint" not in caplog.text + response.text
    durable_activity.assert_not_called()
    assert not response.cookies


def test_provider_revocation_store_outage_cannot_be_treated_as_an_active_reader_session(
    oidc_app, monkeypatch
):
    complete(oidc_app)
    active = Mock(side_effect=ConnectionError("private-revocation-store"))
    monkeypatch.setattr(auth.oidc_logout, "active", active)
    activity = Mock()
    monkeypatch.setattr(auth, "touch_user_activity", activity)
    response = oidc_app.client.get("/v1/user/auth/me")
    assert response.status_code == 503
    assert response.json() == {"detail": "User sessions temporarily unavailable"}
    assert "private-revocation-store" not in response.text
    activity.assert_not_called()
    assert not response.cookies


@pytest.mark.parametrize("later", ["deleted", "outage"])
def test_session_probe_rechecks_the_record_after_initial_authentication(
    oidc_app, monkeypatch, later
):
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_user_session")
    key = auth.key("session", token)
    original = oidc_app.store.get
    reads = 0

    def current_record(requested):
        nonlocal reads
        if requested == key:
            reads += 1
            if reads == 2:
                if later == "deleted":
                    oidc_app.store.delete(key)
                elif later == "outage":
                    raise ConnectionError("private-session-store")
        return original(requested)

    monkeypatch.setattr(oidc_app.store, "get", current_record)
    response = oidc_app.client.get("/v1/user/auth/me")
    assert reads == 2
    assert response.status_code == (503 if later == "outage" else 200)
    assert response.json() == (
        {"detail": "User sessions temporarily unavailable"} if later == "outage" else None
    )
    assert not response.cookies


def test_session_reaching_its_absolute_deadline_after_authentication_is_not_returned_by_probe(
    oidc_app, monkeypatch
):
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_user_session")
    record = json.loads(oidc_app.store.get(auth.key("session", token)))
    record_activity = auth._record_activity

    def after_authentication(user):
        record_activity(user)
        # Advance after authentication completes, before the probe captures its own read clock.
        monkeypatch.setattr(
            auth, "time", SimpleNamespace(time=lambda: record["absolute_expires_at"])
        )

    monkeypatch.setattr(auth, "_record_activity", after_authentication)
    response = oidc_app.client.get("/v1/user/auth/me")
    assert response.status_code == 200 and response.json() is None
    assert not response.cookies


def test_provider_session_ending_after_initial_authentication_hides_the_later_probe_identity(
    oidc_app, monkeypatch
):
    complete(oidc_app)
    active = Mock(side_effect=[True, False])
    monkeypatch.setattr(auth.oidc_logout, "active", active)
    response = oidc_app.client.get("/v1/user/auth/me")
    assert response.status_code == 200 and response.json() is None
    assert active.call_count == 2
    assert not response.cookies


@pytest.mark.parametrize("stored", [b"not-json", b"{}", b"[]"])
def test_corrupt_external_session_record_cannot_claim_successful_logout_or_clear_browser_cookie(
    oidc_app, stored
):
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_user_session")
    key = auth.key("session", token)
    _, expiry = oidc_app.store.values[key]
    oidc_app.store.values[key] = (stored, expiry)
    response = oidc_app.client.post("/v1/user/auth/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 401 and response.json() == {"detail": "Invalid user session"}
    assert oidc_app.store.get(key) == stored
    assert not response.cookies
    assert oidc_app.client.cookies.get("__Host-devfeed_user_session") == token


def test_logout_without_configured_user_origin_fails_before_session_access(oidc_app, monkeypatch):
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_user_session")
    oidc_app.settings.base_url = None
    redis = Mock()
    monkeypatch.setattr(auth, "get_redis", redis)
    response = oidc_app.client.post("/v1/user/auth/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 503 and response.json() == {
        "detail": "User origin is not configured"
    }
    redis.assert_not_called()
    assert not response.cookies
    assert oidc_app.client.cookies.get("__Host-devfeed_user_session") == token


def test_verified_identity_expiring_before_durable_account_save_cannot_open_a_session(
    oidc_app, monkeypatch
):
    identity = auth.oidc.identity

    def checked_identity(*args, **kwargs):
        verified = identity(*args, **kwargs)
        monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: verified["expires_at"]))
        return verified

    monkeypatch.setattr(auth.oidc, "identity", checked_identity)
    save = Mock()
    monkeypatch.setattr(auth, "save_user_with_status", save)
    response = complete(oidc_app)
    assert response.status_code == 302 and "error=login_failed" in response.headers["location"]
    save.assert_not_called()
    assert oidc_app.client.cookies.get("__Host-devfeed_user_session") is None
    assert not any(key.startswith("devfeed:user:session:") for key in oidc_app.store.values)


def test_authenticated_activity_is_debounced(oidc_app, monkeypatch):
    complete(oidc_app)
    activity = []
    monkeypatch.setattr(auth, "touch_user_activity", activity.append)

    assert oidc_app.client.get("/v1/user/auth/me").status_code == 200
    assert oidc_app.client.get("/v1/user/auth/me").status_code == 200

    assert activity == ["00000000-0000-4000-8000-000000000001"]


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


def test_user_client_change_revokes_sessions(oidc_app):
    complete(oidc_app)
    oidc_app.settings.oidc_client_id = "changed-client"
    assert oidc_app.client.get("/v1/user/auth/me").json() is None
    assert oidc_app.client.get("/v1/user/preferences").status_code == 401


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


def test_reader_session_survives_provider_expiry_and_renews_after_overnight(oidc_app, monkeypatch):
    state = oidc_app
    login = complete(state)
    session_cookie = next(
        value for value in login.headers.get_list("set-cookie") if "user_session=" in value
    )
    max_age = int(session_cookie.split("Max-Age=", 1)[1].split(";", 1)[0])
    assert 90 * 86400 - 2 <= max_age <= 90 * 86400
    token = state.client.cookies.get("__Host-devfeed_user_session")
    key = auth.key("session", token)
    initial = json.loads(state.store.get(key))
    assert initial["expires_at"] - initial["renewed_at"] == 30 * 86400
    assert initial["absolute_expires_at"] - initial["renewed_at"] == 90 * 86400
    monkeypatch.setattr(time, "time", lambda: initial["renewed_at"] + 12 * 3600)
    response = state.client.get("/v1/user/auth/me")
    assert response.status_code == 200
    assert response.json()["expires_at"] == int(time.time()) + 30 * 86400
    assert "set-cookie" not in response.headers
    assert json.loads(state.store.get(key))["absolute_expires_at"] == initial["absolute_expires_at"]
    assert "absolute_expires_at" not in response.json()


def test_reader_activity_extends_session_past_initial_idle_window(oidc_app, monkeypatch):
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_user_session")
    key = auth.key("session", token)
    initial = json.loads(oidc_app.store.get(key))
    for day in (20, 40, 60, 80):
        now = initial["renewed_at"] + day * 86400
        monkeypatch.setattr(time, "time", lambda now=now: now)
        response = oidc_app.client.get("/v1/user/auth/me")
        assert response.json()["expires_at"] == min(
            now + 30 * 86400, initial["absolute_expires_at"]
        )
        assert "set-cookie" not in response.headers
    monkeypatch.setattr(time, "time", lambda: initial["absolute_expires_at"] + 1)
    assert oidc_app.client.get("/v1/user/auth/me").json() is None


def test_reader_renewal_is_bounded_and_expired_sessions_stay_expired(oidc_app, monkeypatch):
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_user_session")
    key = auth.key("session", token)
    record = json.loads(oidc_app.store.get(key))
    now = int(time.time())
    record.update(renewed_at=now - 7200, absolute_expires_at=now + 60, expires_at=now + 60)
    oidc_app.store.set(key, json.dumps(record), ex=60)
    response = oidc_app.client.get("/v1/user/auth/me")
    assert response.json()["expires_at"] == now + 60
    assert "set-cookie" not in response.headers
    monkeypatch.setattr(time, "time", lambda: now + 61)
    assert oidc_app.client.get("/v1/user/auth/me").json() is None
    assert oidc_app.store.get(key) is None


def test_reader_renewal_cannot_resurrect_concurrently_revoked_session(oidc_app, monkeypatch):
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_user_session")
    key = auth.key("session", token)
    record = json.loads(oidc_app.store.get(key))
    record["renewed_at"] -= 7200
    oidc_app.store.set(key, json.dumps(record), ex=86400)

    def revoke(*args):
        oidc_app.store.delete(key)
        return None

    monkeypatch.setattr(oidc_app.store, "eval", revoke)
    response = oidc_app.client.get("/v1/user/auth/me")
    assert response.json() is None
    assert "set-cookie" not in response.headers
    assert oidc_app.store.get(key) is None


@pytest.mark.parametrize("missing", ["absolute_expires_at", "renewed_at"])
def test_incomplete_session_records_require_a_new_login(oidc_app, missing):
    complete(oidc_app)
    token = oidc_app.client.cookies.get("__Host-devfeed_user_session")
    key = auth.key("session", token)
    record = json.loads(oidc_app.store.get(key))
    del record[missing]
    oidc_app.store.set(key, json.dumps(record), ex=86400)
    response = oidc_app.client.get("/v1/user/auth/me")
    assert response.json() is None
    assert "set-cookie" not in response.headers
