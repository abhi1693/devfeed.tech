"""Publication-to-inbox transactions, recipient selection and resumable delivery."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.editorial import EditorialDecision, decide_article
from devfeed_core.feed_notifications import expand_feed_notifications
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTopic,
    FeedNotificationEvent,
    NotificationDelivery,
    Source,
    Topic,
    UserAccount,
    UserTopic,
    utcnow,
)
from devfeed_notifications import delivery
from devfeed_notifications.config import Settings as DeliverySettings
from sqlalchemy import delete, event, func, insert, select

pytestmark = pytest.mark.integration


@pytest.fixture
def published_data(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    monkeypatch.setattr(get_settings(), "chimely_user_environment", "users")
    topics = [uuid.uuid4() for _ in range(4)]
    users = [uuid.UUID(int=i + 1) for i in range(7)]
    before = utcnow() - timedelta(days=1)
    with database.begin() as session:
        session.execute(
            insert(Topic),
            [
                dict(
                    id=t,
                    name=f"Topic {i}",
                    slug=f"topic-{i}",
                    kind="technology",
                    status="proposed" if i == 3 else "active",
                )
                for i, t in enumerate(topics)
            ],
        )
        session.execute(
            insert(UserAccount),
            [
                dict(id=u, issuer="https://id.test", subject=str(u), organization_id="org")
                for u in users
            ],
        )
        session.execute(
            insert(UserTopic),
            [
                dict(user_id=users[i], topic_id=topics[t], created_at=before)
                for i, t in [(0, 0), (0, 1), (1, 1), (2, 2), (3, 3), (4, 0)]
            ],
        )
        source = Source(
            name="Publisher",
            feed_url="https://example.test/feed",
            source_type="publisher",
            approval_status="approved",
        )
        article = Article(
            title="Building reliable Python applications",
            url_hash=uuid.uuid4().hex,
            canonical_url="https://example.test/post",
            summary=(
                "This tutorial explains how to build reliable Python applications "
                "with practical examples."
            ),
            language="en",
            content_type="tutorial",
            content_format="article",
            review_status="approved",
            classification_provenance={"developer_relevance": "relevant"},
        )
        article.origins = [
            ArticleOrigin(source=source, entry_key="one", original_url=article.canonical_url)
        ]
        article.topic_links = [
            ArticleTopic(
                topic_id=t,
                role="primary" if i == 0 else "incidental" if i == 2 else "supporting",
                relevance=1,
                evidence="Test",
            )
            for i, t in enumerate(topics)
        ]
        session.add(article)
        session.flush()
        identifier = article.id
    return SimpleNamespace(factory=database, article=identifier, users=users, topics=topics)


def publish(data, *, commit=True):
    with data.factory() as session:
        decide_article(session, data.article, EditorialDecision(action="publish"))
        session.flush()
        assert session.get(FeedNotificationEvent, data.article) is not None
        if commit:
            session.commit()


def test_first_publication_is_atomic_and_republish_does_not_notify_again(published_data):
    d = published_data
    publish(d, commit=False)
    with d.factory() as session:
        assert session.get(FeedNotificationEvent, d.article) is None
        assert session.get(Article, d.article).publication_status == "unpublished"
    publish(d)
    with d.factory.begin() as session:
        decide_article(session, d.article, EditorialDecision(action="unpublish"))
        decide_article(session, d.article, EditorialDecision(action="publish"))
    assert expand_feed_notifications(d.factory) == 3
    assert expand_feed_notifications(d.factory) == 0
    with d.factory() as session:
        assert session.scalar(select(func.count()).select_from(FeedNotificationEvent)) == 1
        rows = session.scalars(
            select(NotificationDelivery).where(NotificationDelivery.audience == "user")
        ).all()
        assert len(rows) == 3
        assert {r.payload["user_id"] for r in rows} == {str(d.users[i]) for i in [0, 1, 4]}
        assert all(
            r.subscriber_id and r.payload["action_url"] == f"/articles/{d.article}" for r in rows
        )


def test_batched_cursor_ignores_late_follows_and_current_unfollows(published_data):
    d = published_data
    publish(d)
    with d.factory.begin() as session:
        session.execute(delete(UserTopic).where(UserTopic.user_id == d.users[4]))
        session.add(UserTopic(user_id=d.users[5], topic_id=d.topics[0]))
    assert expand_feed_notifications(d.factory, batch=1) == 1
    # Simulate retry from an earlier cursor: stable outbox keys prevent duplicates.
    with d.factory.begin() as session:
        session.get(FeedNotificationEvent, d.article).recipient_cursor = None
    assert expand_feed_notifications(d.factory, batch=1) == 1
    assert expand_feed_notifications(d.factory, batch=1) == 1
    assert expand_feed_notifications(d.factory, batch=1) == 0
    with d.factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(NotificationDelivery)
                .where(NotificationDelivery.audience == "user")
            )
            == 2
        )


@pytest.mark.parametrize("change", ["unpublish", "unfollow", "source", "topic"])
def test_pending_delivery_rechecks_eligibility_and_does_not_send(
    published_data, monkeypatch, change
):
    d = published_data
    publish(d)
    expand_feed_notifications(d.factory)
    with d.factory.begin() as session:
        job = session.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.audience == "user",
                NotificationDelivery.payload["user_id"].astext == str(d.users[0]),
            )
        )
        identifier = job.id
        if change == "unfollow":
            session.execute(delete(UserTopic).where(UserTopic.user_id == d.users[0]))
        elif change == "unpublish":
            session.get(Article, d.article).publication_status = "unpublished"
        elif change == "source":
            session.scalar(select(Source)).approval_status = "rejected"
        else:
            for t in d.topics[:2]:
                session.get(Topic, t).status = "rejected"
    monkeypatch.setattr(
        delivery,
        "get_settings",
        lambda: DeliverySettings(
            _env_file=None,
            notifications_enabled=True,
            chimely_api_url="http://chimely.test",
            chimely_user_environment="users",
            chimely_user_api_key="test-key",
        ),
    )
    monkeypatch.setattr(delivery.httpx, "Client", lambda **kw: pytest.fail("Must not send"))
    delivery._deliver(identifier)
    with d.factory() as session:
        assert session.get(NotificationDelivery, identifier).status == "succeeded"
        assert session.get(NotificationDelivery, identifier).error.startswith("Skipped:")


def test_delivery_uses_user_key_and_stable_idempotency_on_retry(published_data, monkeypatch):
    d = published_data
    publish(d)
    expand_feed_notifications(d.factory)
    requests = []
    transport = httpx.MockTransport(
        lambda request: (
            requests.append(request) or httpx.Response(503 if len(requests) == 1 else 201)
        )
    )
    client = httpx.Client
    monkeypatch.setattr(
        delivery.httpx, "Client", lambda **kwargs: client(transport=transport, **kwargs)
    )
    monkeypatch.setattr(
        delivery,
        "get_settings",
        lambda: DeliverySettings(
            _env_file=None,
            notifications_enabled=True,
            chimely_api_url="http://chimely.test",
            chimely_user_environment="users",
            chimely_user_api_key="user-key",
            chimely_admin_environment="admin",
            chimely_admin_api_key="admin-key",
        ),
    )
    with d.factory() as session:
        identifier = session.scalar(
            select(NotificationDelivery.id).where(NotificationDelivery.audience == "user")
        )
    delivery._deliver(identifier)
    with d.factory.begin() as session:
        job = session.get(NotificationDelivery, identifier)
        assert job.status == "queued"
        job.available_at = utcnow() - timedelta(seconds=1)
    delivery._deliver(identifier)
    assert len(requests) == 2 and requests[0].content == requests[1].content
    assert requests[0].url.path == "/v1/notifications"
    assert requests[0].headers["authorization"] == "Bearer user-key"
    import json

    body = json.loads(requests[0].content)
    assert body["subscriber_id"].startswith("user_")
    assert "user_id" not in body["payload"] and "article_id" not in body["payload"]


def test_no_backlog_when_disabled(published_data, monkeypatch):
    d = published_data
    monkeypatch.setattr(get_settings(), "notifications_enabled", False)
    with d.factory.begin() as session:
        decide_article(session, d.article, EditorialDecision(action="publish"))
    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    with d.factory.begin() as session:
        decide_article(session, d.article, EditorialDecision(action="publish"))
    assert expand_feed_notifications(d.factory) == 0


def test_recipient_page_has_constant_database_roundtrips(published_data):
    d = published_data
    before = utcnow() - timedelta(days=1)
    with d.factory.begin() as session:
        ids = [uuid.uuid4() for _ in range(205)]
        session.execute(
            insert(UserAccount),
            [
                dict(id=u, issuer="https://id.test", subject=str(u), organization_id="org")
                for u in ids
            ],
        )
        session.execute(
            insert(UserTopic),
            [dict(user_id=u, topic_id=d.topics[0], created_at=before) for u in ids],
        )
    publish(d)
    statements = []

    def counted(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(get_engine(), "before_cursor_execute", counted)
    try:
        assert expand_feed_notifications(d.factory) == 100
    finally:
        event.remove(get_engine(), "before_cursor_execute", counted)
    assert len(statements) <= 5
    assert expand_feed_notifications(d.factory) == 100
    assert expand_feed_notifications(d.factory) == 8
    assert expand_feed_notifications(d.factory) == 0
