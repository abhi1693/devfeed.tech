"""Reader session probes fail closed across dependency and later-read boundaries.

The signed mock OIDC provider creates real valid session histories. Redis reads
and provider-revocation status are explicit external boundaries; no Lua renewal
atomicity is simulated or claimed by these cases.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_user_api import auth
from redis.exceptions import ConnectionError
from test_user_auth import ORIGIN, complete
from test_user_auth import oidc_app as oidc_app


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
