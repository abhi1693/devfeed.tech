"""Rank ClickHouse opens against a short authoritative business-data snapshot."""

from __future__ import annotations

import struct
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.analytics_article_opens import ArticleOpenAnalytics
from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.models import Article, ArticleLike
from devfeed_core.publication import visible_article
from devfeed_core.schemas import ArticleOut, FeedPage
from devfeed_http.dependencies import run_database
from sqlalchemy import func, select
from sqlalchemy.orm import Session

MAX_METADATA_BYTES = 64 * 1024**2
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _ranking_metadata(session: Session, since: datetime) -> bytes:
    """Serialize bounded external query data without retaining ORM rows.

    One short PostgreSQL statement supplies visibility, tie ordering and likes.
    Its transaction ends before ClickHouse is contacted. Temporary query input
    is not a PostgreSQL analytics table or a persistent ClickHouse copy.
    """
    likes = (
        select(ArticleLike.article_id, func.count().label("likes"))
        .where(ArticleLike.created_at >= since)
        .group_by(ArticleLike.article_id)
        .subquery()
    )
    statement = (
        select(Article.id, Article.feed_at, func.coalesce(likes.c.likes, 0).label("likes"))
        .outerjoin(likes, likes.c.article_id == Article.id)
        .where(visible_article())
        .execution_options(yield_per=1000)
    )
    data = bytearray()
    try:
        for row in session.execute(statement):
            stamp = (
                row.feed_at.astimezone(UTC)
                if row.feed_at.tzinfo
                else row.feed_at.replace(tzinfo=UTC)
            )
            delta = stamp - _EPOCH
            micros = ((delta.days * 86400 + delta.seconds) * 1_000_000) + delta.microseconds
            identity = row.id.int
            data.extend(
                struct.pack("<QQqQ", identity >> 64, identity & (2**64 - 1), micros, int(row.likes))
            )
            if len(data) > MAX_METADATA_BYTES:
                raise AnalyticsUnavailable("Trending metadata exceeds the request memory budget")
        return bytes(data)
    finally:
        session.rollback()


def _articles(session: Session, identities: Sequence[str]) -> list[Article]:
    if not identities:
        return []
    statement = (
        select(Article)
        .options(*PUBLIC_ARTICLE_OPTIONS)
        .where(Article.id.in_([uuid.UUID(identity) for identity in identities]), visible_article())
    )
    articles = {str(article.id): article for article in session.scalars(statement)}
    return [articles[identity] for identity in identities if identity in articles]


def _article_page(session: Session, identities: Sequence[str]) -> list[ArticleOut]:
    return ArticleOut.from_articles(_articles(session, identities))


async def trending_page(
    factory: Callable[[], Session],
    analytics: ArticleOpenAnalytics,
    *,
    since: datetime,
    limit: int,
    cursor: int,
) -> FeedPage:
    """Delegate sorting and deep offsets to a single bounded ClickHouse query."""
    async with analytics.client.external_query_slot():
        metadata = await run_database(factory, _ranking_metadata, since)
        ranked = await analytics.trending(since, metadata, limit=limit, cursor=cursor)
    selected = [row["article_id"] for row in ranked[:limit]]
    articles = await run_database(factory, _article_page, selected)
    return FeedPage(
        items=articles,
        next_cursor=str(cursor + limit)
        if len(ranked) > limit and cursor + limit <= 1_000_000
        else None,
    )
