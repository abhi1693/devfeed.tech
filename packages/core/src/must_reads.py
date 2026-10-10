"""Shared visibility-safe reads of persisted daily Must Reads."""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import raiseload

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTopic,
    Source,
    Topic,
    UserMustRead,
    UserReadingEvent,
    UserRecommendation,
    UserRecommendationState,
    UserSource,
)
from devfeed_core.publication import visible_article
from devfeed_core.user_settings import FeedSettings


def recommendation_eligibility(user_id):
    """A prepared recommendation must still have an eligible topic or source."""
    return or_(
        (UserRecommendation.source_id.is_not(None))
        & Article.origins.any(
            (ArticleOrigin.source_id == UserRecommendation.source_id)
            & ArticleOrigin.source.has(Source.approval_status == "approved")
        )
        & select(UserSource.user_id)
        .where(
            UserSource.user_id == user_id,
            UserSource.source_id == UserRecommendation.source_id,
        )
        .exists(),
        (UserRecommendation.source_id.is_(None))
        & Article.topic_links.any(
            (ArticleTopic.topic_id == UserRecommendation.topic_id)
            & ArticleTopic.role.in_(["primary", "supporting"])
            & ArticleTopic.topic.has(Topic.status == "active")
        ),
    )


def ensure_snapshot(session, account, day, timezone):
    """Create the same stable top-five picks for reader requests and daily push.

    The caller holds the account row lock to serialize selection across devices.
    """
    snapshot = session.get(UserMustRead, (account.id, day))
    state = session.get(UserRecommendationState, account.id)
    preparing = not state or state.invalidated or not state.ranked_at
    if snapshot is not None or preparing:
        return snapshot, preparing
    settings = FeedSettings.model_validate(account.feed_settings)
    rows = session.execute(
        select(Article, UserRecommendation)
        .join(UserRecommendation, UserRecommendation.article_id == Article.id)
        .where(
            UserRecommendation.user_id == account.id,
            visible_article(),
            recommendation_eligibility(account.id),
            Article.language.in_(settings.languages),
            Article.content_type.in_(settings.content_types),
        )
        .order_by(UserRecommendation.position)
        .limit(5)
    ).all()
    picks = []
    for article, entry in rows:
        topic = session.get(Topic, entry.topic_id) if entry.topic_id else None
        source = session.get(Source, entry.source_id) if entry.source_id else None
        seed = session.get(Topic, entry.seed_topic_id) if entry.seed_topic_id else None
        if source:
            reason = f"Because you follow {source.name}"
        elif topic and entry.reason == "followed_topic":
            reason = f"Because you follow {topic.name}"
        elif topic and entry.reason == "liked_topic":
            reason = f"Based on articles you liked about {topic.name}"
        elif seed:
            reason = f"Related to your interest in {seed.name}"
        else:
            reason = "Selected from your recommendations"
        picks.append({"id": str(article.id), "reason": reason})
    if picks:
        snapshot = UserMustRead(
            user_id=account.id, selection_date=day, timezone=timezone, picks=picks
        )
        session.add(snapshot)
        session.flush()
    return snapshot, preparing


def read_snapshot(session, snapshot, settings, *, include_details=True):
    """Read a snapshot without creating picks or claiming their presentation."""
    if snapshot is None:
        return [], {}, []
    ids = [uuid.UUID(pick["id"]) for pick in snapshot.picks]
    statement = select(Article).where(
        Article.id.in_(ids),
        visible_article(),
        Article.language.in_(settings.languages),
        Article.content_type.in_(settings.content_types),
    )
    if include_details:
        statement = statement.options(*PUBLIC_ARTICLE_OPTIONS)
    else:
        statement = statement.options(raiseload("*"))
    by_id = {article.id: article for article in session.scalars(statement)}
    read_ids = list(
        session.scalars(
            select(UserReadingEvent.article_id)
            .where(
                UserReadingEvent.user_id == snapshot.user_id,
                UserReadingEvent.article_id.in_(list(by_id)),
            )
            .distinct()
        )
    )
    reasons = {
        pick["id"]: pick["reason"] for pick in snapshot.picks if uuid.UUID(pick["id"]) in by_id
    }
    return [by_id[id] for id in ids if id in by_id], reasons, read_ids
