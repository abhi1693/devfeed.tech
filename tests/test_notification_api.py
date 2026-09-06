"""Exercise the real session/CSRF boundary and a mock Chimely HTTP transport."""

import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest
from devfeed_admin_api import notifications
from pydantic import SecretStr
from test_admin_auth import complete
from test_admin_auth import logout_headers as csrf_headers
from test_admin_auth import oidc_app as oidc_app

BASE = "/v1/admin/notifications"
INBOX = BASE + "/chimely/v1/inbox/"


@pytest.fixture
def inbox_app(oidc_app, monkeypatch):
    settings = oidc_app.settings
    settings.notifications_enabled = True
    settings.chimely_api_url = "http://chimely.internal:8080"
    settings.chimely_admin_environment = "admin-prod"
    settings.chimely_admin_hmac_secret = SecretStr("private-hmac-secret")
    monkeypatch.setattr(notifications, "get_settings", lambda: settings)
    response = SimpleNamespace(status=200, body={"items": [], "next_cursor": None}, headers={})
    requests, clients = [], []

    def transport(request):
        requests.append(request)
        return httpx.Response(response.status, json=response.body, headers=response.headers)

    def client():
        value = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        clients.append(value)
        return value

    monkeypatch.setattr(notifications, "http_client", client)
    return SimpleNamespace(
        auth=oidc_app,
        client=oidc_app.client,
        requests=requests,
        clients=clients,
        response=response,
        settings=settings,
    )


@pytest.mark.parametrize("path", [BASE + "/config", INBOX + "items", INBOX + "stream"])
def test_requires_admin_before_any_chimely_access(inbox_app, path):
    assert inbox_app.client.get(path).status_code == 401
    assert not inbox_app.requests


def test_config_never_exposes_hmac_or_api_key(inbox_app):
    complete(inbox_app.auth)
    response = inbox_app.client.get(BASE + "/config")
    assert response.status_code == 200 and response.json()["enabled"]
    assert response.json()["environment"] == "admin-prod"
    assert response.json()["subscriber_id"].startswith("admin_")
    assert "private" not in response.text and "hash" not in response.text
    assert response.headers["cache-control"] == "no-store" and not inbox_app.requests


def test_overrides_forged_audience_identity_and_credentials_preserves_filters(inbox_app):
    complete(inbox_app.auth)
    response = inbox_app.client.get(
        INBOX
        + "items?environment=users&subscriber_id=user_victim&subscriber_hash=forged"
        + "&filter=archived&limit=10&cursor=next",
        headers={
            "X-Chimely-Environment": "users",
            "X-Chimely-Subscriber": "user_victim",
            "X-Chimely-Subscriber-Hash": "forged",
            "Authorization": "Bearer forged-key",
            "If-None-Match": '"etag-one"',
        },
    )
    assert response.status_code == 200
    request = inbox_app.requests[0]
    identifier = inbox_app.client.get(BASE + "/config").json()["subscriber_id"]
    assert request.headers["x-chimely-environment"] == "admin-prod"
    assert request.headers["x-chimely-subscriber"] == identifier
    assert (
        request.headers["x-chimely-subscriber-hash"]
        == hmac.new(b"private-hmac-secret", identifier.encode(), hashlib.sha256).hexdigest()
    )
    assert "authorization" not in request.headers and "cookie" not in request.headers
    assert dict(request.url.params) == {"filter": "archived", "limit": "10", "cursor": "next"}
    assert request.headers["if-none-match"] == '"etag-one"'
    assert all(client.is_closed for client in inbox_app.clients)


@pytest.mark.parametrize(
    "path",
    [
        "read-all",
        "seen-all",
        "archive-all",
        "archive-read",
        "broadcasts/bcast_" + "a" * 26 + "/read",
        "notifications/notif_" + "b" * 26 + "/unarchive",
    ],
)
def test_mutations_require_csrf_and_origin(inbox_app, path):
    complete(inbox_app.auth)
    assert inbox_app.client.post(INBOX + path).status_code == 403
    assert not inbox_app.requests
    response = inbox_app.client.post(INBOX + path, headers=csrf_headers(inbox_app.auth))
    assert response.status_code == 200
    assert inbox_app.requests[0].url.path == "/v1/inbox/" + path


def test_preferences_are_protected_bounded_and_forwarded(inbox_app):
    complete(inbox_app.auth)
    body = {"preferences": [{"category": "jobs.ingestion", "channel": "in_app", "enabled": False}]}
    result = inbox_app.client.put(
        INBOX + "preferences", json=body, headers=csrf_headers(inbox_app.auth)
    )
    assert result.status_code == 200
    assert json.loads(inbox_app.requests[-1].content) == body
    result = inbox_app.client.put(
        INBOX + "preferences", content="x" * 20_000, headers=csrf_headers(inbox_app.auth)
    )
    assert result.status_code == 413 and len(inbox_app.requests) == 1


@pytest.mark.parametrize(
    "path",
    [
        "v1/broadcasts",
        "subscribers",
        "admin/keys",
        "unknown",
        "items?limit=1&limit=2",
        "items?cursor=" + "x" * 2049,
    ],
)
def test_proxy_is_not_a_management_or_arbitrary_path_proxy(inbox_app, path):
    complete(inbox_app.auth)
    assert inbox_app.client.get(INBOX + path).status_code in {404, 422}
    assert not inbox_app.requests


def test_etag_304_reaches_sdk_without_body(inbox_app):
    complete(inbox_app.auth)
    inbox_app.response.status, inbox_app.response.headers = 304, {"ETag": '"current"'}
    result = inbox_app.client.get(INBOX + "items")
    assert result.status_code == 304 and not result.content
    assert result.headers["etag"] == '"current"'


@pytest.mark.parametrize("status", [301, 401, 403, 500])
def test_upstream_errors_do_not_leak_details_or_log_out_valid_admin(inbox_app, status):
    complete(inbox_app.auth)
    inbox_app.response.status, inbox_app.response.body = status, {"secret": "private upstream info"}
    result = inbox_app.client.get(INBOX + "counts")
    assert result.status_code == 503 and "private" not in result.text
    assert inbox_app.client.get("/v1/admin/auth/me").status_code == 200


def test_logout_revokes_inbox_access_without_reusable_browser_hash(inbox_app):
    complete(inbox_app.auth)
    assert inbox_app.client.get(INBOX + "counts").status_code == 200
    result = inbox_app.client.post("/v1/admin/auth/logout", headers=csrf_headers(inbox_app.auth))
    assert result.status_code == 204
    assert inbox_app.client.get(INBOX + "counts").status_code == 401
    assert len(inbox_app.requests) == 1


def test_stream_passes_hints_with_no_buffering_and_closes_connections(inbox_app, monkeypatch):
    complete(inbox_app.auth)
    requests, clients = [], []

    def transport(request):
        requests.append(request)
        return httpx.Response(
            200, content=b"event: hint\ndata: {}\n\n", headers={"Content-Type": "text/event-stream"}
        )

    def client():
        value = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        clients.append(value)
        return value

    monkeypatch.setattr(notifications, "http_client", client)
    result = inbox_app.client.get(INBOX + "stream?last_event_id=resume")
    assert result.status_code == 200 and "event: hint" in result.text
    assert result.headers["x-accel-buffering"] == "no"
    assert requests[0].headers["last-event-id"] == "resume"
    assert all(c.is_closed for c in clients)
