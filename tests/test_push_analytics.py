"""Browser-push reporting keeps event cohorts, transport and browser evidence distinct."""

import uuid
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from devfeed_admin_api import auth, push_analytics
from devfeed_admin_api.config import Settings
from devfeed_admin_api.dependencies import get_session
from devfeed_admin_api.main import create_app
from devfeed_core.models import UserAccount, WebPushDelivery, WebPushEvent, WebPushSubscription
from fastapi.testclient import TestClient
from sqlalchemy import text

NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)
START = datetime(2026, 10, 1, tzinfo=UTC)


def test_push_analytics_requires_admin_before_database_access(monkeypatch):
    settings = Settings(
        _env_file=None,
        admin_base_url="https://admin.example",
        oidc_issuer_url="https://identity.example",
        oidc_client_id="test",
        oidc_organization_id="org",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: pytest.fail("Unauthorized report access")
    with TestClient(app) as client:
        assert client.get("/v1/admin/push-analytics").status_code == 401
        assert (
            client.get(
                "/v1/admin/push-analytics", headers={"Authorization": "Bearer token"}
            ).status_code
            == 401
        )


@pytest.mark.parametrize(
    ("displayed", "clicked_from_displayed", "expected"),
    [(0, 0, 0), (4, 1, 25), (3, 1, 33.33), (1, 1, 100)],
)
def test_click_rate_uses_reported_displays_and_never_relay_acceptance(
    displayed, clicked_from_displayed, expected
):
    metrics = push_analytics._metrics(
        {
            "displayed": displayed,
            "clicked": clicked_from_displayed + 1,
            "accepted": 99,
            "clicked_from_displayed": clicked_from_displayed,
        }
    )
    assert metrics.click_rate == expected
    assert "clicked_from_displayed" not in metrics.model_dump()


@pytest.mark.parametrize("days", [-1, 0, 91, 1_000_000])
def test_analytics_helper_rejects_unbounded_days_before_accessing_database(days):
    with pytest.raises(ValueError, match="between 1 and 90"):
        push_analytics.push_analytics_metrics(None, days, now=NOW)


def account():
    identifier = uuid.uuid4()
    return UserAccount(
        id=identifier,
        issuer="https://identity.example",
        subject=str(identifier),
        organization_id="test-org",
        email="private-reader@example.com",
        created_at=START - timedelta(days=1),
    )


def subscription(user, **values):
    identifier = uuid.uuid4()
    return WebPushSubscription(
        id=identifier,
        user_id=user.id,
        consent_id=uuid.uuid4(),
        endpoint=f"https://fcm.googleapis.com/fcm/send/private-{identifier}",
        endpoint_hash=identifier.hex,
        p256dh="private-encryption-key",
        auth="private-auth-key",
        timezone="Asia/Kolkata",
        session_hash="private-session-hash",
        created_at=START - timedelta(days=1),
        authorization_expires_at=values.pop("authorization_expires_at", NOW + timedelta(days=1)),
        **values,
    )


def event(when=START, kind="daily_must_read"):
    identifier = uuid.uuid4()
    return WebPushEvent(
        id=identifier,
        event_key=f"private-publication-{identifier}",
        kind=kind,
        payload={"title": "Private personalized title", "context": {"secret": "private-context"}},
        audience={"kind": "users", "user_ids": [str(uuid.uuid4())]},
        created_at=when,
        expires_at=when + timedelta(hours=24),
    )


def delivery(notification, browser, **values):
    return WebPushDelivery(
        event_id=notification.id,
        user_id=browser.user_id,
        subscription_id=browser.id,
        consent_id=browser.consent_id,
        session_hash=browser.session_hash,
        created_at=notification.created_at,
        **values,
    )


@pytest.mark.integration
@pytest.mark.parametrize("days", [1, 7, 30, 90])
def test_empty_report_zero_fills_bounded_utc_days(database, admin_client, monkeypatch, days):
    monkeypatch.setattr(push_analytics, "utcnow", lambda: NOW)
    response = admin_client.get(f"/v1/admin/push-analytics?days={days}")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["enabled"] is False
    assert result["enabled_subscriptions"] == result["enabled_accounts"] == 0
    assert result["days"] == days
    assert result["totals"] == push_analytics.PushAnalyticsMetrics().model_dump()
    assert len(result["daily"]) == days
    assert result["daily"][0]["date"] == (NOW.date() - timedelta(days=days - 1)).isoformat()
    assert result["daily"][-1]["date"] == NOW.date().isoformat()
    assert result["by_kind"] == []
    assert all(row["published_events"] == row["browser_deliveries"] == 0 for row in result["daily"])


@pytest.mark.integration
def test_report_counts_fanout_once_and_separates_browser_evidence(
    database, admin_client, monkeypatch
):
    monkeypatch.setattr(push_analytics, "utcnow", lambda: NOW)
    monkeypatch.setattr(
        push_analytics, "get_web_push_settings", lambda: SimpleNamespace(web_push_enabled=True)
    )
    with database.begin() as session:
        first, second, expired_account = account(), account(), account()
        session.add_all([first, second, expired_account])
        session.flush()
        browsers = [subscription(first), subscription(first), subscription(second)]
        session.add_all(
            [
                *browsers,
                subscription(expired_account, authorization_expires_at=NOW),
                subscription(first, enabled=False),
            ]
        )
        daily, another_type, no_recipients = event(), event(NOW, "future_example"), event(NOW)
        session.add_all([daily, another_type, no_recipients])
        session.flush()
        session.add_all(
            [
                delivery(
                    daily,
                    browsers[0],
                    status="succeeded",
                    attempts=2,
                    accepted_at=NOW,
                    displayed_at=NOW,
                    clicked_at=NOW,
                    opened_at=NOW,
                ),
                delivery(daily, browsers[1], status="succeeded", attempts=1, accepted_at=NOW),
                delivery(daily, browsers[2], status="succeeded", attempts=0, error="private-error"),
                delivery(another_type, browsers[0], status="failed", attempts=4, clicked_at=NOW),
                delivery(another_type, browsers[1], status="queued", attempts=0),
                delivery(another_type, browsers[2], status="running", attempts=1),
            ]
        )
    response = admin_client.get("/v1/admin/push-analytics?days=7")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["enabled"] is True
    assert result["enabled_subscriptions"] == 3
    assert result["enabled_accounts"] == 2
    assert result["totals"] == {
        "published_events": 3,
        "recipient_accounts": 2,
        "browser_deliveries": 6,
        "accepted": 2,
        "displayed": 1,
        "clicked": 2,
        "opened": 1,
        "failed": 1,
        "skipped": 1,
        "queued": 1,
        "running": 1,
        "retries": 4,
        "click_rate": 100.0,
    }
    assert result["daily"][0]["published_events"] == 1
    assert result["daily"][0]["recipient_accounts"] == 2
    assert result["daily"][0]["accepted"] == 2
    assert result["daily"][-1]["published_events"] == 2
    assert result["daily"][-1]["recipient_accounts"] == 2
    assert result["daily"][-1]["click_rate"] == 0
    daily_kind, future_kind = result["by_kind"]
    assert daily_kind["kind"] == "daily_must_read"
    assert daily_kind["published_events"] == 2
    assert daily_kind["browser_deliveries"] == 3
    assert daily_kind["displayed"] == daily_kind["clicked"] == daily_kind["opened"] == 1
    assert future_kind["kind"] == "future_example"
    assert future_kind["published_events"] == 1
    assert future_kind["clicked"] == 1
    assert future_kind["click_rate"] == 0
    for private in ("private-", "user_id", "subscription_id", "payload", "audience", "error"):
        assert private not in response.text


@pytest.mark.integration
def test_publication_cohort_uses_utc_and_excludes_outside_range(database, monkeypatch):
    monkeypatch.setattr(push_analytics, "utcnow", lambda: NOW)
    with database.begin() as session:
        user = account()
        session.add(user)
        session.flush()
        browser = subscription(user)
        session.add(browser)
        included = [event(START), event(START + timedelta(hours=23, minutes=59)), event(NOW)]
        outside = [
            event(START - timedelta(microseconds=1)),
            event(NOW + timedelta(microseconds=1)),
            event(datetime(2026, 10, 1, 1, tzinfo=timezone(timedelta(hours=14)))),
        ]
        session.add_all([*included, *outside])
        session.flush()
        session.add_all(
            delivery(notification, browser, status="succeeded", accepted_at=NOW, displayed_at=NOW)
            for notification in [*included, *outside]
        )
    with database() as session:
        session.execute(text("SET TIME ZONE 'Pacific/Auckland'"))
        result = push_analytics.push_analytics_metrics(session, 7)
    assert result.generated_at == NOW
    assert result.totals.published_events == result.totals.browser_deliveries == 3
    assert result.totals.recipient_accounts == 1
    assert result.daily[0].published_events == result.daily[0].displayed == 2
    assert result.daily[-1].published_events == result.daily[-1].displayed == 1
    assert all(day.browser_deliveries == 0 for day in result.daily[1:-1])


@pytest.mark.integration
def test_browser_receipts_remain_evidence_before_relay_acceptance_is_recorded(database):
    with database.begin() as session:
        user = account()
        session.add(user)
        session.flush()
        browser, notification = subscription(user), event()
        session.add_all([browser, notification])
        session.flush()
        session.add(
            delivery(
                notification,
                browser,
                status="running",
                attempts=1,
                displayed_at=NOW,
                clicked_at=NOW,
                opened_at=NOW,
            )
        )
    with database() as session:
        result = push_analytics.push_analytics_metrics(session, 7, now=NOW)
    assert result.totals.accepted == 0
    assert result.totals.displayed == result.totals.clicked == result.totals.opened == 1
    assert result.totals.click_rate == 100
    assert result.totals.running == 1


@pytest.mark.integration
@pytest.mark.parametrize("days", [0, 91])
def test_report_rejects_unbounded_day_range(database, admin_client, days):
    assert admin_client.get(f"/v1/admin/push-analytics?days={days}").status_code == 422
