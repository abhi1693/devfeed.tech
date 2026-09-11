"""First-publication notifications for existing topic followers, never broadcasts."""

import hashlib
import json
from datetime import timedelta

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert

from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleTopic,
    FeedNotificationEvent,
    NotificationDelivery,
    Topic,
    UserAccount,
    UserTopic,
    utcnow,
)
from devfeed_core.notifications import NotificationMessage, notification_subscriber_id
from devfeed_core.publication import visible_article

CATEGORY = "feed.topic.new"


def record_publication(session, article, published_at):
    settings = get_settings()
    if not settings.notifications_enabled or not settings.chimely_user_environment:
        return
    session.flush()
    topics = list(
        session.scalars(
            select(ArticleTopic.topic_id)
            .join(Topic)
            .where(
                ArticleTopic.article_id == article.id,
                ArticleTopic.role.in_(["primary", "supporting"]),
                Topic.status == "active",
            )
        )
    )
    if topics:
        session.execute(
            insert(FeedNotificationEvent)
            .values(
                article_id=article.id,
                topic_ids=topics,
                created_at=published_at,
            )
            .on_conflict_do_nothing()
        )


def recipient_ids(event):
    # Use the topic membership index rather than scanning the user directory.
    # Current unfollows and follows created after publication are excluded.
    return (
        select(UserTopic.user_id)
        .join(Topic)
        .join(ArticleTopic, ArticleTopic.topic_id == Topic.id)
        .where(
            UserTopic.created_at <= event.created_at,
            UserTopic.topic_id.in_(event.topic_ids),
            Topic.status == "active",
            ArticleTopic.article_id == event.article_id,
            ArticleTopic.role.in_(["primary", "supporting"]),
        )
        .distinct()
    )


def expand_feed_notifications(factory, batch=100):
    """One locked event/page per tick, with cursor and outbox committed together."""
    settings = get_settings()
    if not settings.notifications_enabled or not settings.chimely_user_environment:
        return 0
    with factory.begin() as session:
        event = session.scalar(
            select(FeedNotificationEvent)
            .where(
                FeedNotificationEvent.completed_at.is_(None),
            )
            .order_by(FeedNotificationEvent.created_at, FeedNotificationEvent.article_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if event is None:
            return 0
        article = session.execute(
            select(Article.title, Article.slug).where(
                Article.id == event.article_id,
                visible_article(),
            )
        ).first()
        if article is None or utcnow() - event.created_at >= timedelta(days=28):
            event.completed_at = utcnow()
            return 0
        candidates = recipient_ids(event)
        if event.recipient_cursor:
            candidates = candidates.where(UserTopic.user_id > event.recipient_cursor)
        page = candidates.order_by(UserTopic.user_id).limit(batch).subquery()
        users = session.execute(
            select(
                UserAccount.id,
                UserAccount.issuer,
                UserAccount.subject,
                UserAccount.organization_id,
            )
            .join(page, page.c.user_id == UserAccount.id)
            .order_by(UserAccount.id)
        ).all()
        message = NotificationMessage(
            category=CATEGORY,
            title=article.title[:200],
            body="New in a topic you follow. Open the article preview to read more.",
            action_url=f"/articles/{article.slug}",
        )
        key = f"feed:{event.article_id}"
        deliveries = []
        for user in users:
            subscriber = notification_subscriber_id(
                audience="user",
                issuer=user.issuer,
                subject=user.subject,
                organization_id=user.organization_id,
            )
            deliveries.append(
                dict(
                    event_key=key,
                    dedup_key=hashlib.sha256(
                        json.dumps(["user", subscriber, key]).encode()
                    ).hexdigest(),
                    audience="user",
                    subscriber_id=subscriber,
                    category=CATEGORY,
                    payload={
                        **message.model_dump(exclude={"category"}),
                        "article_id": str(event.article_id),
                        "user_id": str(user.id),
                    },
                )
            )
        if deliveries:
            session.execute(
                insert(NotificationDelivery)
                .values(deliveries)
                .on_conflict_do_nothing(index_elements=[NotificationDelivery.dedup_key])
            )
            event.recipient_cursor = users[-1].id
        if len(users) < batch:
            event.completed_at = utcnow()
        return len(users)


def delivery_is_current(session, job):
    import uuid

    event = session.get(FeedNotificationEvent, uuid.UUID(job.payload["article_id"]))
    if event is None:
        return False
    user = session.execute(
        select(UserAccount.issuer, UserAccount.subject, UserAccount.organization_id).where(
            UserAccount.id == uuid.UUID(job.payload["user_id"]),
            UserAccount.id.in_(recipient_ids(event)),
            exists(select(Article.id).where(Article.id == event.article_id, visible_article())),
        )
    ).first()
    return user is not None and job.subscriber_id == notification_subscriber_id(
        audience="user",
        issuer=user.issuer,
        subject=user.subject,
        organization_id=user.organization_id,
    )
