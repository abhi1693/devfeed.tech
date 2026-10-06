"""Browser receipt authentication, passive sessions, and idempotent ownership checks."""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core.models import WebPushDelivery, WebPushEvent, WebPushSubscription, utcnow
from devfeed_user_api import auth
from devfeed_user_api import web_push as api
from sqlalchemy import select
from test_user_auth import complete, logout_headers
from test_user_auth import oidc_app as oidc_app
from test_user_web_push import BASE, OWNER, enroll
from test_user_web_push import browser_push as browser_push

pytestmark = pytest.mark.integration


@pytest.fixture
def receipt_data(browser_push):
    state = browser_push
    complete(state.auth)
    registration = enroll(state).json()
    with state.database.begin() as session:
        subscription = session.get(WebPushSubscription, uuid.UUID(registration["id"]))
        event = WebPushEvent(
            event_key="receipt-fixture",
            kind="daily_must_read",
            payload={},
            audience={"kind": "users", "user_ids": [str(OWNER)]},
            created_at=utcnow(),
            expires_at=utcnow() + timedelta(hours=1),
        )
        session.add(event)
        session.flush()
        delivery = WebPushDelivery(
            event_id=event.id,
            subscription_id=subscription.id,
            user_id=OWNER,
            session_hash=subscription.session_hash,
            consent_id=subscription.consent_id,
            attempts=1,
            status="succeeded",
            accepted_at=utcnow(),
        )
        session.add(delivery)
        session.flush()
        return NS(
            state=state,
            event_id=event.id,
            delivery_id=delivery.id,
            subscription_id=subscription.id,
            payload={
                "notification_id": str(event.id),
                "subscription_id": registration["consent_id"],
                "action": "displayed",
            },
        )


def post(data, **overrides):
    return data.state.client.post(
        BASE + "/receipts",
        json={**data.payload, **overrides},
        headers=logout_headers(data.state.auth),
    )


def authorize(data, **overrides):
    return data.state.client.get(
        BASE + "/authorization",
        params={
            "notification_id": data.payload["notification_id"],
            "subscription_id": data.payload["subscription_id"],
            **overrides,
        },
    )


def test_display_authorization_is_passive_and_does_not_create_receipts(receipt_data, monkeypatch):
    data = receipt_data
    token = data.state.client.cookies[api.oidc.cookie_name(data.state.auth.settings, "session")]
    key = auth.key("session", token)
    raw = json.loads(data.state.auth.store.get(key))
    raw["renewed_at"] = 0
    data.state.auth.store.set(key, json.dumps(raw), ex=3600)
    before = data.state.auth.store.get(key)
    activity = Mock()
    monkeypatch.setattr(auth, "_record_activity", activity)
    assert authorize(data).json() == {"authorized": True}
    assert data.state.auth.store.get(key) == before
    activity.assert_not_called()
    with data.state.database() as session:
        delivery = session.get(WebPushDelivery, data.delivery_id)
        assert delivery.displayed_at is None and delivery.clicked_at is None


@pytest.mark.parametrize(
    "reason",
    [
        "unknown_event",
        "unknown_consent",
        "disabled",
        "expired",
        "event_expired",
        "changed_session",
        "changed_consent",
        "different_owner",
        "kind_revoked",
        "not_attempted",
        "newer_enrollment",
        "feature_paused",
    ],
)
def test_display_authorization_rejects_stale_or_unowned_delivery(receipt_data, reason):
    data = receipt_data
    changes = {}
    with data.state.database.begin() as session:
        subscription = session.get(WebPushSubscription, data.subscription_id)
        delivery = session.get(WebPushDelivery, data.delivery_id)
        event = session.get(WebPushEvent, data.event_id)
        if reason == "unknown_event":
            changes["notification_id"] = str(uuid.uuid4())
        elif reason == "unknown_consent":
            changes["subscription_id"] = str(uuid.uuid4())
        elif reason == "disabled":
            subscription.enabled = False
        elif reason == "expired":
            subscription.authorization_expires_at = utcnow() - timedelta(seconds=1)
        elif reason == "event_expired":
            event.expires_at = utcnow() - timedelta(seconds=1)
        elif reason == "changed_session":
            delivery.session_hash = "different-session"
        elif reason == "changed_consent":
            subscription.consent_id = uuid.uuid4()
        elif reason == "different_owner":
            from test_user_web_push import OTHER

            delivery.user_id = OTHER
        elif reason == "kind_revoked":
            subscription.allowed_kinds = []
        elif reason == "not_attempted":
            delivery.attempts = 0
        elif reason == "newer_enrollment":
            subscription.created_at = event.created_at + timedelta(seconds=1)
        elif reason == "feature_paused":
            data.state.settings.web_push_enabled = False
    assert authorize(data, **changes).json() == {"authorized": False}


@pytest.mark.parametrize("reason", ["logout", "idle_expired", "policy_changed", "new_session"])
def test_display_authorization_checks_current_cookie_session(receipt_data, reason):
    data = receipt_data
    token = data.state.client.cookies[api.oidc.cookie_name(data.state.auth.settings, "session")]
    key = auth.key("session", token)
    if reason == "logout":
        data.state.auth.store.delete(key)
    elif reason == "idle_expired":
        raw = json.loads(data.state.auth.store.get(key))
        raw["expires_at"] = 0
        data.state.auth.store.set(key, json.dumps(raw), ex=3600)
    elif reason == "policy_changed":
        data.state.auth.settings.session_ttl_seconds -= 1
    else:
        complete(data.state.auth)
        assert authorize(data).json() == {"authorized": False}
        return
    assert authorize(data).status_code == 401


def test_receipt_bootstrap_has_no_identity_details_and_does_not_renew_or_record_activity(
    receipt_data, monkeypatch
):
    data = receipt_data
    settings = data.state.auth.settings
    token = data.state.client.cookies[api.oidc.cookie_name(settings, "session")]
    key = auth.key("session", token)
    raw = json.loads(data.state.auth.store.get(key))
    raw["renewed_at"] = 0
    data.state.auth.store.set(key, json.dumps(raw), ex=3600)
    before = data.state.auth.store.get(key)
    activity = Mock()
    monkeypatch.setattr(auth, "_record_activity", activity)
    response = data.state.client.get(BASE + "/receipt-session")
    assert response.status_code == 200
    assert response.json() == {"user_id": str(OWNER), "csrf_token": raw["csrf_token"]}
    assert data.state.auth.store.get(key) == before
    assert post(data).status_code == 204
    assert data.state.auth.store.get(key) == before
    activity.assert_not_called()


def test_receipts_require_cookie_session_exact_origin_and_csrf(receipt_data):
    data = receipt_data
    client = data.state.client
    path = BASE + "/receipts"
    assert client.post(path, json=data.payload).status_code == 403
    headers = logout_headers(data.state.auth)
    headers["Origin"] = "https://attacker.example"
    assert client.post(path, json=data.payload, headers=headers).status_code == 403
    client.cookies.clear()
    assert client.get(BASE + "/receipt-session").status_code == 401
    assert (
        client.get(BASE + "/receipt-session", headers={"Authorization": "Bearer agent"}).status_code
        == 401
    )
    assert client.post(path, json=data.payload, headers=headers).status_code == 401
    with data.state.database() as session:
        assert session.get(WebPushDelivery, data.delivery_id).displayed_at is None


@pytest.mark.parametrize("action", ["displayed", "clicked", "opened"])
def test_receipts_are_first_write_only_and_open_is_not_a_read(receipt_data, action, monkeypatch):
    data = receipt_data
    now = utcnow()
    monkeypatch.setattr(api, "utcnow", lambda: now)
    assert post(data, action=action).status_code == 204
    later = now + timedelta(seconds=5)
    monkeypatch.setattr(api, "utcnow", lambda: later)
    assert post(data, action=action).status_code == 204
    with data.state.database() as session:
        delivery = session.get(WebPushDelivery, data.delivery_id)
        assert getattr(delivery, f"{action}_at") == now
        assert delivery.accepted_at is not None
        if action == "opened":
            assert delivery.clicked_at == now
            assert delivery.displayed_at is None
        # No reading, bookmark or article state is changed by browser acknowledgements.
        from devfeed_core.models import ArticleOpen, UserReadingEvent

        assert session.scalar(select(ArticleOpen)) is None
        assert session.scalar(select(UserReadingEvent)) is None


@pytest.mark.parametrize(
    "reason",
    [
        "unknown_event",
        "unknown_consent",
        "disabled",
        "expired",
        "changed_session",
        "changed_consent",
        "different_owner",
        "kind_revoked",
        "not_attempted",
        "too_old",
        "newer_enrollment",
    ],
)
def test_invalid_or_stale_receipts_are_noop_without_disclosing_delivery_identity(
    receipt_data, reason
):
    data = receipt_data
    changes = {}
    with data.state.database.begin() as session:
        subscription = session.get(WebPushSubscription, data.subscription_id)
        delivery = session.get(WebPushDelivery, data.delivery_id)
        event = session.get(WebPushEvent, data.event_id)
        if reason == "unknown_event":
            changes["notification_id"] = str(uuid.uuid4())
        elif reason == "unknown_consent":
            changes["subscription_id"] = str(uuid.uuid4())
        elif reason == "disabled":
            subscription.enabled = False
        elif reason == "expired":
            subscription.authorization_expires_at = utcnow() - timedelta(seconds=1)
        elif reason == "changed_session":
            delivery.session_hash = "different-session"
        elif reason == "changed_consent":
            subscription.consent_id = uuid.uuid4()
        elif reason == "different_owner":
            from test_user_web_push import OTHER

            delivery.user_id = OTHER
        elif reason == "kind_revoked":
            subscription.allowed_kinds = []
        elif reason == "not_attempted":
            delivery.attempts, delivery.accepted_at, delivery.status = 0, None, "queued"
        elif reason == "too_old":
            event.created_at = utcnow() - timedelta(days=31)
        elif reason == "newer_enrollment":
            subscription.created_at = event.created_at + timedelta(seconds=1)
    assert post(data, **changes).status_code == 204
    with data.state.database() as session:
        assert session.get(WebPushDelivery, data.delivery_id).displayed_at is None


def test_feedback_can_race_acceptance_or_open_after_the_relay_ttl(receipt_data):
    data = receipt_data
    with data.state.database.begin() as session:
        delivery = session.get(WebPushDelivery, data.delivery_id)
        delivery.status, delivery.accepted_at = "running", None
        session.get(WebPushEvent, data.event_id).expires_at = utcnow() - timedelta(seconds=1)
    assert post(data, action="opened").status_code == 204
    with data.state.database() as session:
        delivery = session.get(WebPushDelivery, data.delivery_id)
        assert delivery.opened_at is not None and delivery.clicked_at is not None
        assert delivery.accepted_at is None


def test_receipts_do_not_inherit_a_changed_authentication_policy(receipt_data):
    data = receipt_data
    data.state.auth.settings.session_ttl_seconds -= 1
    assert post(data).status_code == 401
    assert data.state.client.get(BASE + "/receipt-session").status_code == 401


def test_receipt_actions_are_fixed_and_disabled_feature_keeps_historical_data(receipt_data):
    data = receipt_data
    assert post(data, action="read").status_code == 422
    assert post(data, title="custom message").status_code == 422
    data.state.settings.web_push_enabled = False
    assert post(data).status_code == 204
    with data.state.database() as session:
        assert session.get(WebPushDelivery, data.delivery_id).displayed_at is None
