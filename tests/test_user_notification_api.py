"""User inbox identity, CSRF and credential isolation at the real session boundary."""

import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest
from devfeed_user_api import notifications
from devfeed_user_api.notification_config import Settings
from test_user_auth import complete, logout_headers
from test_user_auth import oidc_app as oidc_app

BASE = "/v1/user/notifications"
INBOX = BASE + "/chimely/v1/inbox/"


@pytest.fixture
def inbox(oidc_app, monkeypatch):
    settings = Settings(
        _env_file=None,
        notifications_enabled=True,
        chimely_api_url="http://chimely.test",
        chimely_user_environment="users",
        chimely_user_hmac_secret="user-inbox-secret",
        chimely_admin_environment="admin",
    )
    monkeypatch.setattr(notifications, "get_settings", lambda: settings)
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    monkeypatch.setattr(
        notifications,
        "http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    return SimpleNamespace(
        auth=oidc_app, client=oidc_app.client, requests=requests, settings=settings
    )


@pytest.mark.parametrize("path", ["config", "chimely/v1/inbox/items", "chimely/v1/inbox/stream"])
def test_anonymous_users_cannot_access_inbox(inbox, path):
    assert inbox.client.get(BASE + "/" + path).status_code == 401
    assert not inbox.requests


def test_subscriber_is_bound_to_session_and_never_admin_identity(inbox):
    complete(inbox.auth)
    config = inbox.client.get(BASE + "/config").json()
    assert config["enabled"] and config["subscriber_id"].startswith("user_")
    assert "secret" not in str(config)
    result = inbox.client.get(
        INBOX + "items?subscriber_id=admin_victim&environment=admin&limit=10",
        headers={
            "X-Chimely-Subscriber": "admin_victim",
            "Authorization": "Bearer stolen",
            "X-Chimely-Subscriber-Hash": "forged",
        },
    )
    assert result.status_code == 200
    request = inbox.requests[0]
    assert request.headers["x-chimely-environment"] == "users"
    assert request.headers["x-chimely-subscriber"] == config["subscriber_id"]
    assert (
        request.headers["x-chimely-subscriber-hash"]
        == hmac.new(
            b"user-inbox-secret", config["subscriber_id"].encode(), hashlib.sha256
        ).hexdigest()
    )
    assert "cookie" not in request.headers and "authorization" not in request.headers
    assert dict(request.url.params) == {"limit": "10"}
    assert inbox.client.get("/v1/admin/notifications/config").status_code == 404


@pytest.mark.parametrize(
    "path", ["read-all", "seen-all", "notifications/notif_" + "a" * 26 + "/read"]
)
def test_marking_requires_user_csrf_and_logout_revokes_access(inbox, path):
    complete(inbox.auth)
    assert inbox.client.post(INBOX + path).status_code == 403
    assert not inbox.requests
    headers = logout_headers(inbox.auth)
    assert inbox.client.post(INBOX + path, headers=headers).status_code == 200
    assert inbox.client.post("/v1/user/auth/logout", headers=headers).status_code == 204
    assert inbox.client.get(INBOX + "items").status_code == 401
    assert len(inbox.requests) == 1


def test_disabled_user_environment_does_not_fall_back_to_admin(inbox):
    complete(inbox.auth)
    inbox.settings.chimely_user_environment = None
    assert inbox.client.get(BASE + "/config").json() == {
        "enabled": False,
        "environment": None,
        "subscriber_id": None,
    }
    assert inbox.client.get(INBOX + "items").status_code == 503
    assert not inbox.requests


def test_user_hmac_required_for_enabled_inbox():
    with pytest.raises(ValueError, match="USER_HMAC_SECRET"):
        Settings(
            _env_file=None,
            notifications_enabled=True,
            chimely_api_url="http://chimely.test",
            chimely_user_environment="users",
        )


def test_preferences_require_csrf_and_use_only_current_subscriber(inbox):
    body = {"preferences": [{"category": "feed.topic.new", "channel": "in_app", "enabled": False}]}
    assert inbox.client.put(INBOX + "preferences", json=body).status_code == 401
    complete(inbox.auth)
    assert inbox.client.put(INBOX + "preferences", json=body).status_code == 403
    assert not inbox.requests
    headers = {**logout_headers(inbox.auth), "X-Chimely-Subscriber": "admin_victim"}
    assert inbox.client.put(INBOX + "preferences", json=body, headers=headers).status_code == 200
    request = inbox.requests[0]
    assert request.headers["x-chimely-environment"] == "users"
    assert (
        request.headers["x-chimely-subscriber"]
        == inbox.client.get(BASE + "/config").json()["subscriber_id"]
    )
    assert request.content == json.dumps(body, separators=(",", ":")).encode()
