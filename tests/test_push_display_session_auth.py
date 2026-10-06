"""Passive browser display checks retain live cookie authentication without service access."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_user_api import auth, oidc, web_push
from devfeed_user_api.dependencies import get_session
from redis.exceptions import ConnectionError
from test_user_auth import complete
from test_user_auth import oidc_app as oidc_app

BASE = "/v1/user/notifications/push"
PARAMS = {
    "notification_id": "00000000-0000-4000-8000-000000000001",
    "subscription_id": "00000000-0000-4000-8000-000000000002",
}


@pytest.fixture
def passive_push(oidc_app, monkeypatch):
    state = oidc_app
    settings = SimpleNamespace(web_push_enabled=False)
    monkeypatch.setattr(web_push, "get_web_push_settings", lambda: settings)
    monkeypatch.setattr(web_push, "user_settings", lambda: state.settings)
    database = Mock()

    def sessions():
        yield database

    state.client.app.dependency_overrides[get_session] = sessions
    complete(state)
    token = state.client.cookies[oidc.cookie_name(state.settings, "session")]
    return SimpleNamespace(state=state, settings=settings, database=database, token=token)


def test_paused_display_check_keeps_session_and_consent_recoverable(passive_push, monkeypatch):
    data = passive_push
    key = auth.key("session", data.token)
    record = json.loads(data.state.store.get(key))
    record["renewed_at"] = 0
    data.state.store.set(key, json.dumps(record), ex=3600)
    before = data.state.store.get(key)
    activity = Mock()
    monkeypatch.setattr(auth, "_record_activity", activity)
    response = data.state.client.get(BASE + "/authorization", params=PARAMS)
    assert response.status_code == 200
    assert response.json() == {"authorized": False}
    assert data.state.store.get(key) == before
    data.database.scalar.assert_not_called()
    data.database.commit.assert_not_called()
    activity.assert_not_called()


@pytest.mark.parametrize("reason", ["expired", "deleted", "policy_changed", "bearer_only"])
def test_display_check_requires_current_browser_session_even_while_paused(passive_push, reason):
    data = passive_push
    key = auth.key("session", data.token)
    if reason == "expired":
        record = json.loads(data.state.store.get(key))
        record["expires_at"] = 0
        data.state.store.set(key, json.dumps(record), ex=3600)
    elif reason == "deleted":
        data.state.store.delete(key)
    elif reason == "policy_changed":
        data.state.settings.session_ttl_seconds -= 1
    else:
        data.state.client.cookies.clear()
    response = data.state.client.get(
        BASE + "/authorization", params=PARAMS, headers={"Authorization": "Bearer agent"}
    )
    assert response.status_code == 401
    data.database.scalar.assert_not_called()


def test_display_check_fails_closed_when_session_store_is_unavailable(passive_push, monkeypatch):
    data = passive_push
    monkeypatch.setattr(data.state.store, "get", Mock(side_effect=ConnectionError("Unavailable")))
    assert data.state.client.get(BASE + "/authorization", params=PARAMS).status_code == 503
    data.database.scalar.assert_not_called()


@pytest.mark.parametrize("field", ["subscription_id", "notification_id"])
def test_display_check_requires_valid_opaque_identifiers(passive_push, field):
    data = passive_push
    assert (
        data.state.client.get(
            BASE + "/authorization", params={**PARAMS, field: "invalid"}
        ).status_code
        == 422
    )
    data.database.scalar.assert_not_called()
