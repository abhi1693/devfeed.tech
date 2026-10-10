"""Daily frequency, stable ranking, consent isolation and encrypted relay delivery."""

import base64
import json
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import http_ece
import httpcore
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.job_dispatch import dispatch_jobs
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    DailyMustReadPush,
    UserAccount,
    UserMustRead,
    UserReadingEvent,
    UserRecommendation,
    UserRecommendationState,
    WebPushDelivery,
    WebPushEvent,
    WebPushSubscription,
    utcnow,
)
from devfeed_core.web_push import (
    PublicWebPushSettings,
    next_push_datetime,
    recover_web_push,
    retry_push,
    schedule_daily_pushes,
    validate_endpoint,
    validate_subscription,
)
from devfeed_core.worker_queues import worker_queues
from devfeed_notifications import web_push
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from sqlalchemy import delete, select, update
from test_user_personalization import user_data as user_data
from test_user_recommendations import prepare

NOW = datetime(2026, 10, 6, 9, tzinfo=UTC)


def encode(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


@pytest.fixture
def push_keys():
    sender = ec.generate_private_key(ec.SECP256R1())
    receiver = ec.generate_private_key(ec.SECP256R1())
    public = encode(
        sender.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    private = encode(sender.private_numbers().private_value.to_bytes(32, "big"))
    p256dh = encode(
        receiver.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    auth = encode(b"browser-auth-key")
    settings = web_push.Settings(
        _env_file=None,
        web_push_enabled=True,
        web_push_public_key=public,
        web_push_private_key=private,
        web_push_subject="mailto:maintainer@devfeed.tech",
    )
    return NS(settings=settings, receiver=receiver, sender=sender, p256dh=p256dh, auth=auth)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fcm.googleapis.com/fcm/send/opaque",
        "https://updates.push.services.mozilla.com/wpush/v2/opaque",
        "https://web.push.apple.com/opaque",
        "https://wns2-bl2p.notify.windows.com/w/?token=opaque",
    ],
)
def test_only_real_browser_relay_hosts_are_accepted(endpoint, push_keys):
    validate_subscription(endpoint, push_keys.p256dh, push_keys.auth, "Asia/Kolkata")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/opaque",
        "https://fcm.googleapis.com:8443/fcm/send/opaque",
        "https://fcm.googleapis.com.evil.test/fcm/send/opaque",
        "https://fcm.googleapis.com@127.0.0.1/fcm/send/opaque",
        "https://user@fcm.googleapis.com/fcm/send/opaque",
        "https://127.0.0.1/push",
        "https://fcm.googleapis.com/fcm/send/opaque#fragment",
        "https://fcm.googleapis.com/",
        "https://fcm.googleapis.com/fcm/send/\nopaque",
        "https://fcm.googleapis.com\\evil.test/fcm/send/opaque",
    ],
)
def test_endpoint_policy_rejects_ssrf_and_credential_bypasses(endpoint):
    with pytest.raises(ValueError):
        validate_endpoint(endpoint)


def test_sender_pins_dns_and_rejects_even_allowlisted_private_addresses(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(FeedError, match="private or reserved"):
        web_push.send_request("https://fcm.googleapis.com/fcm/send/opaque", {}, b"encrypted")


def test_public_configuration_does_not_require_or_expose_private_credentials(push_keys):
    settings = PublicWebPushSettings(
        _env_file=None,
        web_push_enabled=True,
        web_push_public_key=push_keys.settings.web_push_public_key,
    )
    assert set(settings.model_dump()) == {
        "web_push_enabled",
        "web_push_public_key",
        "web_push_delivery_hour",
    }
    with pytest.raises(ValueError, match="private key"):
        web_push.Settings(_env_file=None, **settings.model_dump())
    assert "notifications" in worker_queues(
        "background", ai_enabled=False, notifications_enabled=False, web_push_enabled=True
    )
    assert not JOB_DEFINITIONS["web-push"].admin_visible


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:3000", "http://127.0.0.1:3000", "http://[::1]:3000", "https://reader.test"],
)
def test_reader_origin_supports_secure_loopback_development(push_keys, origin):
    settings = web_push.Settings(
        _env_file=None,
        **{**push_keys.settings.model_dump(), "web_push_site_url": origin},
    )
    assert settings.web_push_site_url == origin


@pytest.mark.parametrize(
    "origin",
    [
        "http://192.168.1.101:3000",
        "http://reader.test",
        "http://localhost.evil.test:3000",
        "http://localhost@evil.test:3000",
        "http://user:password@localhost:3000",
        "http://localhost:0",
        "http://localhost:3000/path",
        "http://localhost:3000?redirect=evil",
        "http://localhost:3000#fragment",
    ],
)
def test_reader_origin_rejects_insecure_nonloopback_or_nonorigin_urls(push_keys, origin):
    with pytest.raises(ValueError, match="reader origin"):
        web_push.Settings(
            _env_file=None,
            **{**push_keys.settings.model_dump(), "web_push_site_url": origin},
        )


@pytest.mark.parametrize(
    "now,timezone,expected",
    [
        (
            datetime(2026, 10, 6, 3, 29, tzinfo=UTC),
            "Asia/Kolkata",
            datetime(2026, 10, 6, 3, 30, tzinfo=UTC),
        ),
        (
            datetime(2026, 10, 6, 3, 30, tzinfo=UTC),
            "Asia/Kolkata",
            datetime(2026, 10, 7, 3, 30, tzinfo=UTC),
        ),
        (
            datetime(2026, 3, 7, 15, tzinfo=UTC),
            "America/New_York",
            datetime(2026, 3, 8, 13, tzinfo=UTC),
        ),
        (
            datetime(2026, 10, 31, 14, tzinfo=UTC),
            "America/New_York",
            datetime(2026, 11, 1, 14, tzinfo=UTC),
        ),
    ],
)
def test_next_morning_respects_local_time_and_dst(now, timezone, expected):
    assert next_push_datetime(now, timezone) == expected


@pytest.mark.parametrize(
    "attempts,seconds,retryable,expected",
    [
        (1, 1800, True, "queued"),
        (4, 1800, True, "failed"),
        (1, 30, True, "failed"),
        (1, 1800, False, "failed"),
    ],
)
def test_retry_is_bounded_by_attempts_and_local_midnight(attempts, seconds, retryable, expected):
    job = WebPushDelivery(attempts=attempts, lease_token=uuid.uuid4(), dispatched_at=NOW)
    retry_push(job, "relay unavailable", NOW, NOW + timedelta(seconds=seconds), retryable=retryable)
    assert job.status == expected
    assert job.lease_token is job.lease_until is job.dispatched_at is None


@pytest.mark.parametrize(
    "hint,remaining,state",
    [
        (7200, 10800, "queued"),
        (7200, 7200, "failed"),
        (86400, 10800, "failed"),
        (10**20, 10800, "failed"),
    ],
)
def test_retry_respects_relay_minimum_even_when_longer_than_one_hour(hint, remaining, state):
    job = WebPushDelivery(attempts=1)
    retry_push(job, "rate limited", NOW, NOW + timedelta(seconds=remaining), retry_after=hint)
    assert job.status == state
    if state == "queued":
        assert job.available_at == NOW + timedelta(seconds=hint)


def test_relay_retry_after_accepts_seconds_and_http_date_without_clamping():
    assert web_push.push_retry_after("7200", NOW) == 7200
    assert web_push.push_retry_after("Tue, 06 Oct 2026 11:00:00 GMT", NOW) == 7200
    assert web_push.push_retry_after("9999999999", NOW) == 9999999999
    assert web_push.push_retry_after("unavailable", NOW) == 0


def test_payload_uses_real_encryption_vapid_and_stable_daily_identity(push_keys):
    from devfeed_core.push_notifications import WebPushMessage

    subscription = NS(
        id=uuid.uuid4(),
        consent_id=uuid.uuid4(),
        endpoint="https://fcm.googleapis.com/fcm/send/opaque",
        p256dh=push_keys.p256dh,
        auth=push_keys.auth,
        authorization_expires_at=NOW + timedelta(days=1),
    )
    event = NS(
        id=uuid.uuid4(),
        expires_at=NOW + timedelta(hours=15),
    )
    message = WebPushMessage(
        kind="daily_must_read",
        title="A useful article",
        body="Your personalized must-read for today.",
        action_url="/articles/useful-article",
        context={"claim_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4())},
    )
    endpoint, headers, body = web_push.build_request(
        subscription, event, message, push_keys.settings, NOW
    )
    plain = json.loads(
        http_ece.decrypt(
            body,
            private_key=push_keys.receiver,
            auth_secret=base64.urlsafe_b64decode(push_keys.auth + "=" * (-len(push_keys.auth) % 4)),
            version="aes128gcm",
        )
    )
    assert plain["data"] == {
        "kind": "daily_must_read",
        "notification_id": str(event.id),
        "url": "/articles/useful-article",
        "subscription_id": str(subscription.consent_id),
        "expires_at": int(event.expires_at.timestamp()),
    }
    assert "web_push" not in plain
    assert plain["notification"]["tag"] == f"push-{event.id}"
    assert plain["notification"]["navigate"] == "https://devfeed.tech/articles/useful-article"
    token = headers["Authorization"].split("t=", 1)[1].split(",", 1)[0]
    claims = jwt.decode(
        token,
        push_keys.sender.public_key(),
        algorithms=["ES256"],
        audience="https://fcm.googleapis.com",
        options={"verify_exp": False},
    )
    assert claims["sub"] == "mailto:maintainer@devfeed.tech"
    assert int(headers["TTL"]) == 15 * 3600
    assert len(headers["Topic"]) <= 32 and headers["Content-Encoding"] == "aes128gcm"
    assert b"A useful article" not in body


@pytest.fixture
def push_data(user_data, database, push_keys, monkeypatch):
    client, user, _ = prepare(user_data, database)
    now = utcnow().replace(hour=9, minute=0, second=0, microsecond=0)
    subscriptions = []
    with database.begin() as session:
        session.get(UserAccount, user).created_at = now - timedelta(days=1)
        for index in range(2):
            sub = WebPushSubscription(
                user_id=user,
                endpoint=f"https://fcm.googleapis.com/fcm/send/browser-{index}",
                endpoint_hash=f"browser-{index}",
                p256dh=push_keys.p256dh,
                auth=push_keys.auth,
                timezone="UTC",
                session_hash=f"session-{index}",
                authorization_expires_at=now + timedelta(days=30),
                next_push_at=now,
                created_at=now - timedelta(days=1),
            )
            session.add(sub)
            session.flush()
            subscriptions.append(sub.id)
    monkeypatch.setattr(web_push, "utcnow", lambda: now)
    monkeypatch.setattr(web_push, "get_settings", lambda: push_keys.settings)
    monkeypatch.setattr(
        web_push, "session_authorization_expires_at", lambda *a: now + timedelta(days=1)
    )
    monkeypatch.setenv("DEVFEED_WEB_PUSH_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_WEB_PUSH_PUBLIC_KEY", push_keys.settings.web_push_public_key)
    from devfeed_core.config import get_settings
    from devfeed_core.web_push import get_web_push_settings

    monkeypatch.setattr(get_settings(), "web_push_enabled", True)
    get_web_push_settings.cache_clear()
    from devfeed_core import push_notifications

    monkeypatch.setattr(push_notifications, "get_web_push_settings", lambda: push_keys.settings)
    return NS(
        factory=database,
        client=client,
        user=user,
        subscriptions=subscriptions,
        now=now,
        settings=push_keys.settings,
    )


def schedule(data):
    from devfeed_core.push_notifications import expand_web_push_events

    count = schedule_daily_pushes(data.factory, now=data.now, settings=data.settings)
    expand_web_push_events(data.factory, now=data.now)
    return count


@pytest.mark.integration
def test_daily_claim_and_generic_outbox_commit_before_browser_expansion(push_data):
    from devfeed_core.push_notifications import expand_web_push_events

    data = push_data
    assert schedule_daily_pushes(data.factory, now=data.now, settings=data.settings) == 1
    with data.factory() as session:
        claim = session.scalar(select(DailyMustReadPush))
        event = session.get(WebPushEvent, claim.event_id)
        assert event.kind == "daily_must_read"
        assert event.event_key == f"daily-must-read:{data.user}:{data.now.date()}"
        assert event.payload["context"] == {"claim_id": str(claim.id), "user_id": str(data.user)}
        assert event.payload["title"] == claim.title
        assert event.payload["body"] == "Your personalized must-read for today."
        assert event.expires_at == claim.expires_at
        assert event.expanded_at is None
        assert session.scalar(select(WebPushDelivery)) is None
    assert expand_web_push_events(data.factory, now=data.now) == 1
    with data.factory() as session:
        jobs = list(session.scalars(select(WebPushDelivery)))
        assert len(jobs) == 2 and all(job.user_id == data.user for job in jobs)
        assert all(job.event_id == event.id for job in jobs)


@pytest.mark.integration
def test_publisher_failure_rolls_back_daily_claim_snapshot_and_next_schedule(
    push_data, monkeypatch
):
    from devfeed_core import push_notifications

    data = push_data
    monkeypatch.setattr(
        push_notifications, "enqueue_web_push", Mock(side_effect=RuntimeError("publisher failed"))
    )
    with pytest.raises(RuntimeError, match="publisher failed"):
        schedule_daily_pushes(data.factory, now=data.now, settings=data.settings)
    with data.factory() as session:
        assert session.scalar(select(WebPushEvent)) is None
        assert session.scalar(select(DailyMustReadPush)) is None
        assert session.scalar(select(UserMustRead)) is None
        assert session.scalar(select(WebPushDelivery)) is None
        assert all(
            session.get(WebPushSubscription, identifier).next_push_at == data.now
            for identifier in data.subscriptions
        )


@pytest.mark.integration
def test_other_notification_consent_does_not_enable_daily_must_reads(push_data):
    data = push_data
    with data.factory.begin() as session:
        session.execute(update(WebPushSubscription).values(allowed_kinds=["future_kind"]))
    assert schedule(data) == 0
    with data.factory() as session:
        assert session.scalar(select(WebPushEvent)) is None
        assert session.scalar(select(DailyMustReadPush)) is None
        assert session.scalar(select(WebPushDelivery)) is None


@pytest.mark.integration
def test_reenrollment_after_publication_cannot_recruit_an_older_event(push_data):
    from devfeed_core.push_notifications import expand_web_push_events

    data = push_data
    assert schedule_daily_pushes(data.factory, now=data.now, settings=data.settings) == 1
    later = data.now + timedelta(minutes=5)
    with data.factory.begin() as session:
        sub = session.get(WebPushSubscription, data.subscriptions[0])
        sub.created_at, sub.consent_id = later, uuid.uuid4()
    assert expand_web_push_events(data.factory, now=later) == 1
    with data.factory() as session:
        jobs = list(session.scalars(select(WebPushDelivery)))
        assert len(jobs) == 1
        assert jobs[0].subscription_id == data.subscriptions[1]


@pytest.mark.integration
def test_generic_recovery_uses_event_expiry_without_a_daily_claim(push_data):
    data = push_data
    with data.factory.begin() as session:
        event = WebPushEvent(
            event_key="independent-event:recovery",
            kind="daily_must_read",
            payload={"title": "A server event"},
            audience={"kind": "users", "user_ids": [str(data.user)]},
            created_at=data.now,
            expires_at=data.now + timedelta(minutes=5),
        )
        session.add(event)
        session.flush()
        sub = session.get(WebPushSubscription, data.subscriptions[0])
        job = WebPushDelivery(
            event_id=event.id,
            user_id=data.user,
            subscription_id=sub.id,
            session_hash=sub.session_hash,
            consent_id=sub.consent_id,
            status="running",
            attempts=1,
            created_at=data.now,
            available_at=data.now,
            lease_until=data.now - timedelta(seconds=1),
            lease_token=uuid.uuid4(),
        )
        session.add(job)
        session.flush()
        identifier = job.id
    assert recover_web_push(data.factory, 10, data.now) == 1
    with data.factory() as session:
        job = session.get(WebPushDelivery, identifier)
        assert job.status == "queued" and job.available_at == data.now + timedelta(seconds=30)
        assert session.scalar(select(DailyMustReadPush)) is None


@pytest.mark.integration
def test_concurrent_schedulers_select_same_daily_snapshot_and_one_event(push_data):
    data = push_data
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: schedule(data), range(2))) == 1
    assert schedule(data) == 0
    with data.factory() as session:
        events = list(session.scalars(select(DailyMustReadPush)))
        deliveries = list(session.scalars(select(WebPushDelivery)))
        snapshot = session.get(UserMustRead, (data.user, data.now.date()))
        assert len(events) == 1 and len(deliveries) == 2
        assert str(events[0].article_id) == snapshot.picks[0]["id"]
        assert all(job.event_id == events[0].event_id for job in deliveries)
        assert all(job.user_id == data.user for job in deliveries)
        assert snapshot.presented_at is None
        assert events[0].expires_at == data.now.replace(hour=0) + timedelta(days=1)
    with data.factory.begin() as session:
        session.execute(
            update(WebPushSubscription).values(timezone="Asia/Tokyo", next_push_at=data.now)
        )
    assert schedule(data) == 0


@pytest.mark.integration
def test_catalog_deletion_preserves_daily_frequency_claim(push_data):
    data = push_data
    assert schedule(data) == 1
    with data.factory.begin() as session:
        event = session.scalar(select(DailyMustReadPush))
        article_id, event_id = event.article_id, event.id
        session.execute(delete(Article).where(Article.id == article_id))
        session.execute(update(WebPushSubscription).values(next_push_at=data.now))
    assert schedule(data) == 0
    with data.factory() as session:
        assert session.get(DailyMustReadPush, event_id) is not None


@pytest.mark.integration
def test_all_read_picks_and_expired_sessions_do_not_receive_fallbacks(push_data):
    data = push_data
    daily = data.client.get("/v1/user/must-reads?timezone=UTC").json()
    with data.factory.begin() as session:
        session.add_all(
            UserReadingEvent(
                user_id=data.user,
                read_date=data.now.date(),
                article_id=uuid.UUID(item["id"]),
                occurred_at=data.now,
            )
            for item in daily["items"]
        )
    assert schedule(data) == 0
    data.now += timedelta(days=1)
    with data.factory.begin() as session:
        session.execute(update(WebPushSubscription).values(authorization_expires_at=data.now))
    assert schedule(data) == 0
    with data.factory() as session:
        assert session.scalar(select(DailyMustReadPush)) is None


@pytest.mark.integration
def test_selection_excludes_read_and_previously_accepted_articles(push_data, monkeypatch):
    data = push_data
    assert schedule(data) == 1
    monkeypatch.setattr(web_push, "send_request", lambda *a: (201, 0))
    with data.factory() as session:
        jobs = list(session.scalars(select(WebPushDelivery)))
        first = session.scalar(select(DailyMustReadPush))
        first_article = first.article_id
    for job in jobs:
        web_push.deliver_web_push(str(job.id))
    data.now += timedelta(days=1)
    monkeypatch.setattr(web_push, "utcnow", lambda: data.now)
    assert schedule(data) == 1
    with data.factory() as session:
        event = session.scalar(
            select(DailyMustReadPush).where(DailyMustReadPush.selection_date == data.now.date())
        )
        assert event.article_id != first_article
        snapshot = session.get(UserMustRead, (data.user, data.now.date()))
        assert str(event.article_id) == snapshot.picks[1]["id"]


@pytest.mark.integration
def test_read_top_pick_is_skipped_and_preparing_users_retry_fairly(push_data):
    data = push_data
    daily = data.client.get("/v1/user/must-reads?timezone=UTC").json()
    top = uuid.UUID(daily["items"][0]["id"])
    with data.factory.begin() as session:
        session.add(
            UserReadingEvent(
                user_id=data.user, read_date=data.now.date(), article_id=top, occurred_at=data.now
            )
        )
    assert schedule(data) == 1
    with data.factory() as session:
        assert session.scalar(select(DailyMustReadPush.article_id)) == uuid.UUID(
            daily["items"][1]["id"]
        )
    data.now += timedelta(days=1)
    with data.factory.begin() as session:
        session.get(UserRecommendationState, data.user).invalidated = True
    assert schedule(data) == 0
    with data.factory() as session:
        assert session.get(
            WebPushSubscription, data.subscriptions[0]
        ).next_push_at == data.now + timedelta(minutes=5)


@pytest.mark.integration
@pytest.mark.parametrize(
    "status,state,enabled",
    [
        (201, "succeeded", True),
        (404, "succeeded", False),
        (410, "succeeded", False),
        (429, "queued", True),
        (503, "queued", True),
        (302, "failed", True),
        (401, "failed", True),
    ],
)
def test_per_browser_delivery_acceptance_expiry_backoff_and_no_redirects(
    push_data, monkeypatch, status, state, enabled
):
    data = push_data
    assert schedule(data) == 1
    requests = []
    monkeypatch.setattr(
        web_push, "send_request", lambda *args: requests.append(args) or (status, 120)
    )
    with data.factory() as session:
        job = session.scalar(
            select(WebPushDelivery).where(WebPushDelivery.subscription_id == data.subscriptions[0])
        )
        identifier = job.id
    web_push.deliver_web_push(str(identifier))
    web_push.deliver_web_push(str(identifier))
    assert len(requests) == 1
    with data.factory() as session:
        job = session.get(WebPushDelivery, identifier)
        assert job.status == state
        assert session.get(WebPushSubscription, data.subscriptions[0]).enabled == enabled
        assert (job.accepted_at is not None) == (status == 201)
        other = session.scalar(
            select(WebPushDelivery).where(WebPushDelivery.subscription_id == data.subscriptions[1])
        )
        assert other.status == "queued" and other.attempts == 0
        if state == "queued":
            assert job.available_at == data.now + timedelta(seconds=120)


@pytest.mark.integration
@pytest.mark.parametrize(
    "reason",
    [
        "disabled",
        "rebound",
        "reactivated",
        "expired",
        "revoked",
        "read",
        "withdrawn",
        "source-unfollowed",
        "recommendation-unlinked",
        "session-unavailable",
        "network-unavailable",
    ],
)
def test_delivery_rechecks_consent_session_and_article_before_transport(
    push_data, monkeypatch, reason
):
    data = push_data
    assert schedule(data) == 1
    with data.factory.begin() as session:
        job = session.scalar(
            select(WebPushDelivery).where(WebPushDelivery.subscription_id == data.subscriptions[0])
        )
        identifier = job.id
        sub = session.get(WebPushSubscription, data.subscriptions[0])
        event = session.scalar(
            select(DailyMustReadPush).where(DailyMustReadPush.event_id == job.event_id)
        )
        if reason == "disabled":
            sub.enabled = False
        elif reason == "rebound":
            sub.session_hash = "new-browser-session"
        elif reason == "reactivated":
            sub.consent_id = uuid.uuid4()
        elif reason == "expired":
            event.expires_at = data.now
            session.get(WebPushEvent, job.event_id).expires_at = data.now
        elif reason == "read":
            session.add(
                UserReadingEvent(
                    user_id=data.user,
                    read_date=data.now.date(),
                    article_id=event.article_id,
                    occurred_at=data.now,
                )
            )
        elif reason == "withdrawn":
            session.get(Article, event.article_id).publication_status = "unpublished"
        elif reason == "recommendation-unlinked":
            session.execute(
                delete(UserRecommendation).where(
                    UserRecommendation.user_id == data.user,
                    UserRecommendation.article_id == event.article_id,
                )
            )
        elif reason == "source-unfollowed":
            entry = session.get(UserRecommendation, (data.user, event.article_id))
            entry.source_id = session.scalar(
                select(ArticleOrigin.source_id).where(ArticleOrigin.article_id == event.article_id)
            )
    if reason == "revoked":
        monkeypatch.setattr(web_push, "session_authorization_expires_at", lambda *a: None)
    elif reason == "session-unavailable":
        monkeypatch.setattr(
            web_push,
            "session_authorization_expires_at",
            Mock(side_effect=RedisConnectionError("unavailable")),
        )
    request = (
        Mock(side_effect=httpcore.ConnectError("unavailable"))
        if reason == "network-unavailable"
        else Mock(return_value=(201, 0))
    )
    monkeypatch.setattr(web_push, "send_request", request)
    web_push.deliver_web_push(str(identifier))
    assert request.call_count == int(reason == "network-unavailable")
    with data.factory() as session:
        job = session.get(WebPushDelivery, identifier)
        assert job.accepted_at is None
        assert job.status == (
            "queued" if reason in {"session-unavailable", "network-unavailable"} else "succeeded"
        )


@pytest.mark.integration
def test_recovery_and_redis_dispatch_use_existing_notifications_queue(push_data):
    data = push_data
    assert schedule(data) == 1
    queue = NS(enqueue=Mock(return_value=NS(id="rq-job")))
    assert dispatch_jobs(data.factory, queue, 10, data.now, kind="web-push") == 2
    assert all(
        call.args[0] == "devfeed_notifications.web_push.deliver_web_push"
        for call in queue.enqueue.call_args_list
    )
    with data.factory.begin() as session:
        job = session.scalar(select(WebPushDelivery))
        identifier = job.id
        job.status, job.attempts = "running", 1
        job.lease_until, job.lease_token = data.now - timedelta(seconds=1), uuid.uuid4()
    assert recover_web_push(data.factory, 10, data.now) == 1
    assert recover_web_push(data.factory, 10, data.now) == 0
    with data.factory() as session:
        job = session.get(WebPushDelivery, identifier)
        assert job.status == "queued" and job.available_at == data.now + timedelta(seconds=30)


@pytest.mark.parametrize(
    "record,expected",
    [
        (None, False),
        ({"user_id": "wrong", "expires_at": 9999999999, "absolute_expires_at": 9999999999}, False),
        ({"user_id": "user", "expires_at": 1, "absolute_expires_at": 9999999999}, False),
        (
            {"user_id": "user", "expires_at": 9999999999, "absolute_expires_at": 9999999999},
            False,
        ),
        (
            {
                "user_id": "user",
                "expires_at": 9999999999,
                "absolute_expires_at": 9999999999,
                "policy": "current-policy",
            },
            True,
        ),
        (
            {
                "user_id": "user",
                "expires_at": 9999999999,
                "absolute_expires_at": 9999999999,
                "policy": "old-policy",
            },
            False,
        ),
    ],
)
def test_live_session_validation_fails_closed(monkeypatch, record, expected):
    client = NS(mget=lambda *keys: (json.dumps(record) if record else None, b"current-policy"))
    monkeypatch.setattr(web_push, "create_redis", lambda *a, **kw: nullcontext(client))
    actual = web_push.session_authorization_expires_at(
        NS(user_id="user", session_hash="opaque"), NOW
    )
    assert actual == (datetime.fromtimestamp(9999999999, UTC) if expected else None)


def test_missing_session_policy_retries_without_authorizing_delivery(monkeypatch):
    client = NS(mget=lambda *keys: (b"{}", None))
    monkeypatch.setattr(web_push, "create_redis", lambda *a, **kw: nullcontext(client))
    with pytest.raises(RedisError, match="policy is temporarily unavailable"):
        web_push.session_authorization_expires_at(NS(user_id="user", session_hash="opaque"), NOW)


@pytest.mark.parametrize(
    "event_seconds,subscription_seconds,idle_seconds,absolute_seconds",
    [
        (30, 60, 90, 120),
        (60, 30, 90, 120),
        (60, 90, 30, 120),
        (60, 90, 120, 30),
    ],
)
def test_encrypted_expiry_and_relay_ttl_stop_at_the_earliest_authorization_deadline(
    push_keys, monkeypatch, event_seconds, subscription_seconds, idle_seconds, absolute_seconds
):
    from devfeed_core.push_types import WebPushMessage

    user_id = uuid.uuid4()
    subscription = NS(
        user_id=user_id,
        session_hash="opaque",
        consent_id=uuid.uuid4(),
        endpoint="https://fcm.googleapis.com/fcm/send/opaque",
        p256dh=push_keys.p256dh,
        auth=push_keys.auth,
        authorization_expires_at=NOW + timedelta(seconds=subscription_seconds),
    )
    record = {
        "user_id": str(user_id),
        "expires_at": (NOW + timedelta(seconds=idle_seconds)).timestamp(),
        "absolute_expires_at": (NOW + timedelta(seconds=absolute_seconds)).timestamp(),
        "policy": "current-policy",
    }
    client = Mock(mget=Mock(return_value=(json.dumps(record), b"current-policy")))
    monkeypatch.setattr(web_push, "create_redis", lambda *args, **kwargs: nullcontext(client))
    authorized_until = web_push.session_authorization_expires_at(subscription, NOW)
    assert authorized_until == NOW + timedelta(seconds=min(idle_seconds, absolute_seconds))
    event = NS(id=uuid.uuid4(), expires_at=NOW + timedelta(seconds=event_seconds))
    message = WebPushMessage(
        kind="daily_must_read",
        title="A useful article",
        body="Your personalized must-read for today.",
        action_url="/articles/useful-article",
        context={"claim_id": str(uuid.uuid4()), "user_id": str(user_id)},
    )
    _, headers, encrypted = web_push.build_request(
        subscription, event, message, push_keys.settings, NOW, authorized_until=authorized_until
    )
    plain = json.loads(
        http_ece.decrypt(
            encrypted,
            private_key=push_keys.receiver,
            auth_secret=base64.urlsafe_b64decode(push_keys.auth + "=" * (-len(push_keys.auth) % 4)),
            version="aes128gcm",
        )
    )
    seconds = min(event_seconds, subscription_seconds, idle_seconds, absolute_seconds)
    assert int(headers["TTL"]) == seconds
    assert plain["data"]["expires_at"] == int((NOW + timedelta(seconds=seconds)).timestamp())


@pytest.mark.parametrize(
    "record",
    [
        {"expires_at": NOW.timestamp(), "absolute_expires_at": NOW.timestamp() + 60},
        {"expires_at": NOW.timestamp() + 60, "absolute_expires_at": NOW.timestamp()},
        {"expires_at": "invalid", "absolute_expires_at": NOW.timestamp() + 60},
        {"expires_at": 1e99, "absolute_expires_at": 1e99},
        {"expires_at": float("nan"), "absolute_expires_at": float("nan")},
    ],
)
def test_live_session_deadline_rejects_expired_and_malformed_timestamps(monkeypatch, record):
    client = NS(
        mget=lambda *keys: (
            json.dumps({"user_id": "user", "policy": "current-policy", **record}),
            b"current-policy",
        )
    )
    monkeypatch.setattr(web_push, "create_redis", lambda *args, **kwargs: nullcontext(client))
    assert (
        web_push.session_authorization_expires_at(NS(user_id="user", session_hash="opaque"), NOW)
        is None
    )
