"""Follower notification paging and payload contracts over admitted repository reads.

The supplied audience rows already satisfy the database's follow/time/visibility
query. These tests do not implement or prove that query or outbox uniqueness.
"""

import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import feed_notifications as notifications
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.models import FeedNotificationEvent, NotificationDelivery
from devfeed_core.notifications import notification_subscriber_id
from test_processing_retry_eligibility_unit import scenario
from test_source_readiness_unit import NOW, configuration


@contextmanager
def scoped(session):
    yield session


@pytest.fixture
def runtime(monkeypatch):
    settings = configuration(notifications_enabled=True, chimely_user_environment="reader")
    monkeypatch.setattr(notifications, "get_settings", lambda: settings)
    monkeypatch.setattr(notifications, "utcnow", lambda: NOW)
    published_at = NOW - timedelta(minutes=1)
    monkeypatch.setattr("test_article_lifecycle.utcnow", lambda: published_at)
    article, facts = scenario()
    assert derive_publication_readiness(article, **facts).stage == "ready"
    article.publication_status = "published"
    article.published_to_feed_at = facts["now"]
    event = FeedNotificationEvent(
        article_id=article.id,
        topic_ids=[article.topic_links[0].topic_id],
        source_ids=[article.origins[0].source_id],
        created_at=article.published_to_feed_at,
        recipient_cursor=None,
        completed_at=None,
    )
    session = Mock()
    session.scalar.return_value = event
    factory = SimpleNamespace(begin=lambda: scoped(session))
    return SimpleNamespace(
        settings=settings, article=article, event=event, session=session, factory=factory
    )


@pytest.mark.parametrize("disabled", ["flag", "environment"])
def test_disabled_notifications_leave_publication_and_expansion_without_outbox_work(
    runtime, disabled
):
    if disabled == "flag":
        runtime.settings.notifications_enabled = False
    else:
        runtime.settings.chimely_user_environment = ""
    notifications.record_publication(runtime.session, runtime.article, NOW)
    assert notifications.expand_feed_notifications(runtime.factory) == 0
    runtime.session.flush.assert_not_called()
    runtime.session.scalar.assert_not_called()
    runtime.session.execute.assert_not_called()


@pytest.mark.parametrize("audience", ["topic", "source", "both", "none"])
def test_publication_records_only_the_read_destinations_and_original_publication_time(
    runtime, audience
):
    topics = runtime.event.topic_ids if audience in {"topic", "both"} else []
    sources = runtime.event.source_ids if audience in {"source", "both"} else []
    runtime.session.scalars.side_effect = [topics, sources]
    notifications.record_publication(
        runtime.session, runtime.article, runtime.article.published_to_feed_at
    )
    runtime.session.flush.assert_called_once()
    if audience == "none":
        runtime.session.execute.assert_not_called()
    else:
        values = runtime.session.execute.call_args.args[0].compile().params
        assert values == {
            "article_id": runtime.article.id,
            "topic_ids": topics,
            "source_ids": sources,
            "created_at": runtime.article.published_to_feed_at,
        }


@pytest.mark.parametrize("stop", ["no-event", "withdrawn", "expired"])
def test_expansion_finishes_unavailable_or_expired_events_without_deliveries(
    runtime, stop, monkeypatch
):
    expanded_at = NOW + timedelta(days=28) if stop == "expired" else NOW
    monkeypatch.setattr(notifications, "utcnow", lambda: expanded_at)
    if stop == "no-event":
        runtime.session.scalar.return_value = None
    else:
        runtime.session.execute.return_value.first.return_value = (
            None if stop == "withdrawn" else runtime.article
        )
    assert notifications.expand_feed_notifications(runtime.factory) == 0
    assert runtime.event.completed_at == (None if stop == "no-event" else expanded_at)
    assert runtime.session.execute.call_count == (0 if stop == "no-event" else 1)


@pytest.mark.parametrize("full_page", [False, True])
@pytest.mark.parametrize("ai_title", [False, True])
def test_expansion_builds_private_identity_bound_deliveries_and_resumable_pages(
    runtime, full_page, ai_title
):
    runtime.event.recipient_cursor = uuid.UUID(int=1)
    runtime.article.title = "Original editorial title"
    runtime.article.ai_title = "A" * 220 if ai_title else None
    users = [
        SimpleNamespace(
            id=uuid.UUID(int=i),
            issuer="https://identity.example",
            subject=f"reader-{i}",
            organization_id="readers",
        )
        for i in range(2, 4 if full_page else 3)
    ]
    first, second = Mock(), Mock()
    first.first.return_value = runtime.article
    second.all.return_value = users
    runtime.session.execute.side_effect = [first, second, Mock()]

    assert notifications.expand_feed_notifications(runtime.factory, batch=2) == len(users)

    assert runtime.event.recipient_cursor == users[-1].id
    assert runtime.event.completed_at == (None if full_page else NOW)
    parameters = runtime.session.execute.call_args.args[0].compile().params
    for index, user in enumerate(users):
        subscriber = notification_subscriber_id(
            audience="user",
            issuer=user.issuer,
            subject=user.subject,
            organization_id=user.organization_id,
        )
        key = f"feed:{runtime.article.id}"
        assert parameters[f"audience_m{index}"] == "user"
        assert parameters[f"subscriber_id_m{index}"] == subscriber
        assert parameters[f"event_key_m{index}"] == key
        assert (
            parameters[f"dedup_key_m{index}"]
            == hashlib.sha256(json.dumps(["user", subscriber, key]).encode()).hexdigest()
        )
        assert parameters[f"category_m{index}"] == "feed.topic.new"
        payload = parameters[f"payload_m{index}"]
        assert payload == {
            "title": "A" * 200 if ai_title else runtime.article.title,
            "body": "New from a topic or source you follow. Open the article preview to read more.",
            "action_url": f"/articles/{runtime.article.slug}",
            "severity": "info",
            "article_id": str(runtime.article.id),
            "user_id": str(user.id),
        }
        assert user.subject not in json.dumps(payload) and user.issuer not in json.dumps(payload)


def test_empty_audience_finishes_event_without_changing_last_recipient(runtime):
    cursor = runtime.event.recipient_cursor = uuid.UUID(int=9)
    first, second = Mock(), Mock()
    first.first.return_value = runtime.article
    second.all.return_value = []
    runtime.session.execute.side_effect = [first, second]
    assert notifications.expand_feed_notifications(runtime.factory) == 0
    assert runtime.event.completed_at == NOW and runtime.event.recipient_cursor == cursor
    assert runtime.session.execute.call_count == 2


@pytest.mark.parametrize(
    "change", ["none", "event-removed", "no-longer-eligible", "identity-rotated"]
)
def test_delivery_requires_a_current_eligible_identity_matching_the_original_subscriber(
    runtime, change
):
    user_id = uuid.uuid4()
    identity = SimpleNamespace(
        issuer="https://identity.example", subject="reader-123", organization_id="readers"
    )
    subscriber = notification_subscriber_id(audience="user", **vars(identity))
    delivery = NotificationDelivery(
        id=uuid.uuid4(),
        audience="user",
        category="feed.topic.new",
        event_key=f"feed:{runtime.article.id}",
        dedup_key=hashlib.sha256(b"delivery").hexdigest(),
        subscriber_id=subscriber,
        payload={"article_id": str(runtime.article.id), "user_id": str(user_id)},
        status="queued",
        attempts=0,
        created_at=NOW,
        available_at=NOW,
    )
    runtime.session.get.return_value = None if change == "event-removed" else runtime.event
    if change == "identity-rotated":
        identity.subject = "new-reader-identity"
    runtime.session.execute.return_value.first.return_value = (
        None if change == "no-longer-eligible" else identity
    )
    assert notifications.delivery_is_current(runtime.session, delivery) is (change == "none")
    runtime.session.get.assert_called_once_with(FeedNotificationEvent, runtime.article.id)
    if change == "event-removed":
        runtime.session.execute.assert_not_called()
