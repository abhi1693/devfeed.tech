"""Real browser-session ownership, consent, and revocation for daily alerts."""

import json
import uuid
from base64 import urlsafe_b64encode
from datetime import timedelta
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from devfeed_core.models import UserAccount, WebPushSubscription, utcnow
from devfeed_user_api import auth, oidc, web_push
from devfeed_user_api.dependencies import get_session
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from test_user_auth import complete, logout_headers
from test_user_auth import oidc_app as oidc_app

pytestmark = pytest.mark.integration
BASE = "/v1/user/notifications/push"
OWNER = uuid.UUID("00000000-0000-4000-8000-000000000001")
OTHER = uuid.UUID("00000000-0000-4000-8000-000000000002")


def encode(value):
    return urlsafe_b64encode(value).rstrip(b"=").decode()


@pytest.fixture
def browser_push(oidc_app, database, monkeypatch):
    point = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    settings = SimpleNamespace(
        web_push_enabled=True, web_push_public_key=encode(point), web_push_delivery_hour=9
    )
    monkeypatch.setattr(web_push, "get_web_push_settings", lambda: settings)
    monkeypatch.setattr(web_push, "user_settings", lambda: oidc_app.settings)

    def sessions():
        with database() as session:
            yield session

    oidc_app.client.app.dependency_overrides[get_session] = sessions
    with database.begin() as session:
        session.add_all(
            UserAccount(
                id=identifier,
                issuer="https://identity.example",
                subject=str(identifier),
                organization_id="org-1",
            )
            for identifier in (OWNER, OTHER)
        )
    payload = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/test-browser",
        "keys": {"p256dh": encode(point), "auth": encode(b"x" * 16)},
        "timezone": "Asia/Kolkata",
    }
    return SimpleNamespace(
        auth=oidc_app, client=oidc_app.client, settings=settings, payload=payload, database=database
    )


def enroll(state, **overrides):
    return state.client.post(
        BASE + "/subscriptions",
        json={**state.payload, **overrides},
        headers=logout_headers(state.auth),
    )


def test_browser_session_required_and_config_has_no_signing_credentials(browser_push):
    state = browser_push
    for path in ("/config", "/subscriptions"):
        assert state.client.get(BASE + path).status_code == 401
        assert (
            state.client.get(BASE + path, headers={"Authorization": "Bearer token"}).status_code
            == 401
        )
    complete(state.auth)
    assert state.client.get(BASE + "/config").json() == {
        "enabled": True,
        "public_key": state.settings.web_push_public_key,
        "delivery_hour": 9,
        "delivery_timezone": None,
    }
    state.settings.web_push_enabled = False
    assert state.client.get(BASE + "/config").json()["public_key"] is None
    assert enroll(state).status_code == 503


def test_consent_is_private_idempotent_and_scheduled_for_next_local_morning(browser_push):
    state = browser_push
    complete(state.auth)
    first = enroll(state)
    assert first.status_code == 200, first.text
    identifier = first.json()["id"]
    with state.database() as session:
        original_due = session.get(WebPushSubscription, uuid.UUID(identifier)).next_push_at
    repeated = enroll(state).json()
    assert repeated["id"] == identifier
    assert repeated["consent_id"] == first.json()["consent_id"]
    assert state.client.get(BASE + "/config").json()["delivery_timezone"] == "Asia/Kolkata"
    result = state.client.get(BASE + "/subscriptions").json()["subscriptions"]
    assert len(result) == 1 and result[0]["enabled"]
    assert set(result[0]) == {"id", "consent_id", "endpoint_hash", "enabled", "timezone"}
    assert state.payload["endpoint"] not in json.dumps(result)
    assert state.payload["keys"]["auth"] not in json.dumps(result)
    with state.database() as session:
        row = session.get(WebPushSubscription, uuid.UUID(identifier))
        assert row.user_id == OWNER
        assert row.next_push_at > utcnow()
        assert row.next_push_at == original_due
        assert row.authorization_expires_at > utcnow()
    assert state.client.delete(BASE + "/subscriptions/" + identifier).status_code == 403
    assert (
        state.client.delete(
            BASE + "/subscriptions/" + identifier, headers=logout_headers(state.auth)
        ).status_code
        == 204
    )
    assert not state.client.get(BASE + "/subscriptions").json()["subscriptions"][0]["enabled"]
    reenabled = enroll(state).json()
    assert reenabled["enabled"]
    assert reenabled["consent_id"] != first.json()["consent_id"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/send/test",
        "https://127.0.0.1/push",
        "https://169.254.169.254/push",
        "https://fcm.googleapis.com.evil.example/send/test",
        "https://fcm.googleapis.com:8443/send/test",
        "https://user:password@fcm.googleapis.com/send/test",
    ],
)
def test_untrusted_push_destinations_are_rejected(browser_push, endpoint):
    complete(browser_push.auth)
    response = enroll(browser_push, endpoint=endpoint)
    assert response.status_code == 422
    assert endpoint not in response.text
    assert browser_push.client.get(BASE + "/subscriptions").json() == {"subscriptions": []}


def test_csrf_timezone_and_browser_selected_owner_are_rejected(browser_push):
    state = browser_push
    complete(state.auth)
    assert state.client.post(BASE + "/subscriptions", json=state.payload).status_code == 403
    assert enroll(state, timezone="Invalid/Timezone").status_code == 422
    assert enroll(state, user_id=str(OTHER)).status_code == 422
    assert state.client.get(BASE + "/subscriptions").json() == {"subscriptions": []}


def test_other_accounts_cannot_list_or_disable_a_browser_registration(browser_push):
    state = browser_push
    complete(state.auth)
    original = enroll(state).json()
    identifier = original["id"]
    cookie = state.client.cookies.get(oidc.cookie_name(state.auth.settings, "session"))
    session_key = auth.key("session", cookie)
    record = json.loads(state.auth.store.get(session_key))
    record["user_id"] = str(OTHER)
    state.auth.store.set(session_key, json.dumps(record), ex=3600)
    assert state.client.get(BASE + "/subscriptions").json() == {"subscriptions": []}
    assert (
        state.client.delete(
            BASE + "/subscriptions/" + identifier, headers=logout_headers(state.auth)
        ).status_code
        == 404
    )
    with state.database() as session:
        assert session.get(WebPushSubscription, uuid.UUID(identifier)).enabled
    # Possession of the real subscription keys allows an explicit account switch.
    rebound = enroll(state).json()
    assert rebound["id"] == identifier
    assert rebound["consent_id"] != original["consent_id"]
    with state.database() as session:
        assert session.get(WebPushSubscription, uuid.UUID(identifier)).user_id == OTHER


def test_logout_and_sign_in_switch_revoke_server_consent(browser_push):
    state = browser_push
    complete(state.auth)
    identifier = enroll(state).json()["id"]
    assert (
        state.client.post("/v1/user/auth/logout", headers=logout_headers(state.auth)).status_code
        == 204
    )
    with state.database() as session:
        assert not session.get(WebPushSubscription, uuid.UUID(identifier)).enabled
    complete(state.auth)
    assert enroll(state).json()["enabled"]
    complete(state.auth)
    with state.database() as session:
        assert not session.get(WebPushSubscription, uuid.UUID(identifier)).enabled


def test_expired_registration_is_not_reported_as_enabled(browser_push):
    state = browser_push
    complete(state.auth)
    identifier = enroll(state).json()["id"]
    with state.database.begin() as session:
        session.get(WebPushSubscription, uuid.UUID(identifier)).authorization_expires_at = (
            utcnow() - timedelta(seconds=1)
        )
    assert not state.client.get(BASE + "/subscriptions").json()["subscriptions"][0]["enabled"]
    with state.database() as session:
        assert len(list(session.scalars(select(WebPushSubscription)))) == 1


def test_account_switch_revokes_live_session_even_if_push_database_fails(browser_push, monkeypatch):
    state = browser_push
    complete(state.auth)
    cookie = state.client.cookies.get(oidc.cookie_name(state.auth.settings, "session"))
    session_key = auth.key("session", cookie)
    assert state.auth.store.get(session_key)

    def unavailable(token):
        raise SQLAlchemyError("Disposable push store failure")

    monkeypatch.setattr(web_push, "revoke_browser_push", unavailable)
    # The new login fails closed; the previous identity is already revoked.
    complete(state.auth)
    assert state.auth.store.get(session_key) is None
    assert state.client.get(BASE + "/subscriptions").status_code == 401
