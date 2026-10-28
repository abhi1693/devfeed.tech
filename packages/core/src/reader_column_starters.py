"""Small personal workspace suggestions from current follows and bounded recent likes."""

from __future__ import annotations

import uuid
from itertools import zip_longest

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from devfeed_core.models import (
    Article,
    ArticleLike,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    Source,
    Tag,
    Topic,
    UserSource,
    UserTag,
    UserTopic,
)
from devfeed_core.publication import visible_article
from devfeed_core.user_settings import FeedSettings, SelectionColumnFeed

MAX_STARTER_FEEDS = 4


def personal_column_starters(
    session: Session, user_id: uuid.UUID, settings: FeedSettings
) -> list[SelectionColumnFeed]:
    """Inspect at most four eligible candidates per signal, without a recommendation rebuild."""
    eligible_article = (
        visible_article(),
        Article.language.in_(settings.languages),
        Article.content_type.in_(settings.content_types),
    )
    topic_has_articles = (
        select(ArticleTopic.article_id)
        .join(Article, Article.id == ArticleTopic.article_id)
        .where(
            ArticleTopic.topic_id == Topic.id,
            ArticleTopic.role.in_(["primary", "supporting"]),
            *eligible_article,
        )
        .correlate(Topic)
        .exists()
    )
    followed_topics = session.scalars(
        select(Topic.id)
        .join(UserTopic, UserTopic.topic_id == Topic.id)
        .where(UserTopic.user_id == user_id, Topic.status == "active", topic_has_articles)
        .order_by(Topic.id)
        .limit(MAX_STARTER_FEEDS)
    ).all()
    followed_sources = session.scalars(
        select(Source.id)
        .join(UserSource, UserSource.source_id == Source.id)
        .where(
            UserSource.user_id == user_id,
            Source.approval_status == "approved",
            select(ArticleOrigin.article_id)
            .join(Article, Article.id == ArticleOrigin.article_id)
            .where(ArticleOrigin.source_id == Source.id, *eligible_article)
            .correlate(Source)
            .exists(),
        )
        .order_by(Source.id)
        .limit(MAX_STARTER_FEEDS)
    ).all()
    followed_tags = session.scalars(
        select(Tag.id)
        .join(UserTag, UserTag.tag_id == Tag.id)
        .where(
            UserTag.user_id == user_id,
            select(ArticleTag.article_id)
            .join(Article, Article.id == ArticleTag.article_id)
            .where(ArticleTag.tag_id == Tag.id, *eligible_article)
            .correlate(Tag)
            .exists(),
        )
        .order_by(Tag.id)
        .limit(MAX_STARTER_FEEDS)
    ).all()
    recent_likes = (
        select(ArticleLike.article_id)
        .where(ArticleLike.user_id == user_id)
        .order_by(ArticleLike.created_at.desc(), ArticleLike.article_id)
        .limit(100)
        .subquery()
    )
    liked_topics = session.scalars(
        select(Topic.id)
        .select_from(recent_likes)
        .join(Article, Article.id == recent_likes.c.article_id)
        .join(ArticleTopic, ArticleTopic.article_id == Article.id)
        .join(Topic, Topic.id == ArticleTopic.topic_id)
        .where(
            visible_article(),
            Topic.status == "active",
            ArticleTopic.role.in_(["primary", "supporting"]),
            topic_has_articles,
        )
        .group_by(Topic.id)
        .order_by(func.count().desc(), Topic.id)
        .limit(MAX_STARTER_FEEDS)
    ).all()
    buckets = (
        [SelectionColumnFeed(kind="topic", id=identifier) for identifier in followed_topics],
        [SelectionColumnFeed(kind="source", id=identifier) for identifier in followed_sources],
        [SelectionColumnFeed(kind="tag", id=identifier) for identifier in followed_tags],
        [SelectionColumnFeed(kind="topic", id=identifier) for identifier in liked_topics],
    )
    selected = []
    seen = set()
    for candidates in zip_longest(*buckets):
        for candidate in candidates:
            if candidate is None or (candidate.kind, candidate.id) in seen:
                continue
            selected.append(candidate)
            seen.add((candidate.kind, candidate.id))
            if len(selected) == MAX_STARTER_FEEDS:
                return selected
    return selected
