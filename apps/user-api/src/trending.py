"""Merge recent ClickHouse opens with transactional likes for reader ranking."""

import heapq
import uuid
from datetime import datetime

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.models import Article, ArticleLike
from devfeed_core.publication import visible_article
from devfeed_core.schemas import ArticleOut, FeedPage
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

PAGE_SIZE = 1000


def _likes_page(session, since, after):
    statement = (
        select(ArticleLike.article_id.label("identity"), func.count().label("likes"))
        .where(ArticleLike.created_at >= since)
        .group_by(ArticleLike.article_id)
        .order_by(ArticleLike.article_id)
        .limit(PAGE_SIZE)
    )
    if after is not None:
        statement = statement.where(ArticleLike.article_id > uuid.UUID(after))
    try:
        return [(str(row.identity), int(row.likes)) for row in session.execute(statement)]
    finally:
        # Do not occupy a pool connection while ClickHouse serves the next page.
        session.rollback()


def _visible(session, identities):
    statement = select(Article.id, Article.feed_at).where(
        Article.id.in_([uuid.UUID(identity) for identity in identities]), visible_article()
    )
    try:
        return {str(row.id): row.feed_at for row in session.execute(statement)}
    finally:
        session.rollback()


def _articles(session, identities):
    if not identities:
        return []
    statement = (
        select(Article)
        .options(*PUBLIC_ARTICLE_OPTIONS)
        .where(Article.id.in_([uuid.UUID(identity) for identity in identities]), visible_article())
    )
    articles = {str(article.id): article for article in session.scalars(statement)}
    return [articles[identity] for identity in identities if identity in articles]


class _Pages:
    """Read one source in stable ID order without retaining earlier pages."""

    def __init__(self, fetch):
        self.fetch = fetch
        self.rows = []
        self.index = 0
        self.after = None
        self.done = False

    async def peek(self):
        if self.index >= len(self.rows) and not self.done:
            self.rows = await self.fetch(self.after)
            self.index = 0
            if len(self.rows) < PAGE_SIZE:
                self.done = True
            if self.rows:
                self.after = self.rows[-1][0]
        return self.rows[self.index] if self.index < len(self.rows) else None

    async def take(self):
        row = await self.peek()
        if row is not None:
            self.index += 1
        return row


async def trending_page(session, analytics, *, since: datetime, limit: int, cursor: int):
    """Rank all public activity exactly, retaining only the requested top-k."""

    async def fetch_likes(after):
        return await run_in_threadpool(_likes_page, session, since, after)

    async def fetch_opens(after):
        rows = await analytics.recent_page(since, after=after, limit=PAGE_SIZE)
        return [(row["article_id"], int(row["opens"])) for row in rows]

    likes, opens = _Pages(fetch_likes), _Pages(fetch_opens)
    heap: list[tuple[int, datetime, str]] = []
    capacity = cursor + limit + 1
    pending: list[tuple[str, int]] = []

    async def collect():
        if not pending:
            return
        visible = await run_in_threadpool(_visible, session, [identity for identity, _ in pending])
        for identity, score in pending:
            feed_at = visible.get(identity)
            if feed_at is None:
                continue
            item = (score, feed_at, identity)
            if len(heap) < capacity:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
        pending.clear()

    while True:
        like, opened = await likes.peek(), await opens.peek()
        if like is None and opened is None:
            break
        if opened is None or (like is not None and like[0] < opened[0]):
            identity, count = await likes.take()
            score = 3 * count
        elif like is None or opened[0] < like[0]:
            identity, score = await opens.take()
        else:
            identity, count = await likes.take()
            _, open_count = await opens.take()
            score = 3 * count + open_count
        pending.append((identity, score))
        if len(pending) == PAGE_SIZE:
            await collect()
    await collect()
    ranked = sorted(heap, reverse=True)
    selected = [item[2] for item in ranked[cursor : cursor + limit]]
    articles = await run_in_threadpool(_articles, session, selected)
    return FeedPage(
        items=ArticleOut.from_articles(articles),
        next_cursor=str(cursor + limit)
        if len(ranked) > cursor + limit and cursor + limit <= 1_000_000
        else None,
    )
