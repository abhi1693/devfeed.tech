"""Bounded, immutable public-feed batches with recency-first publisher diversity."""

import hashlib
import json
import uuid
from collections import Counter, deque
from datetime import datetime, timedelta
from heapq import heapify, heappop, heappush

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.cache import CacheUnavailable, get_cache
from devfeed_core.models import Article, ArticleOrigin, Source, utcnow
from devfeed_core.schemas import ArticleOut, FeedPage
from fastapi import HTTPException
from sqlalchemy import literal, select, tuple_

BATCH_SIZE = 480
RETENTION_SECONDS = 3 * 3600


def diverse_ids(rows, publishers):
    result: list[str] = []
    previous = None
    counts: Counter[uuid.UUID | None] = Counter()
    pending = deque(rows)
    while pending:
        floor = pending[0].feed_at - timedelta(hours=24)
        queues: dict[uuid.UUID | None, deque[tuple[int, str]]] = {}
        while pending and pending[0].feed_at > floor:
            row = pending.popleft()
            publisher = publishers.get(row.id)
            queues.setdefault(publisher, deque()).append((len(rows) - len(pending), str(row.id)))
        # Only the newest remaining article of each publisher can win. Keep those
        # heads in a heap instead of rescanning every article for every position.
        heads = [(counts[publisher], queue[0][0], publisher) for publisher, queue in queues.items()]
        heapify(heads)
        while heads:
            if len(result) % 8 == 0:
                counts.clear()
                heads = [(0, index, publisher) for _, index, publisher in heads]
                heapify(heads)
            chosen = heappop(heads)
            if chosen[2] == previous and heads and (heads[0][0] < 2 or chosen[0] >= 2):
                alternative = heappop(heads)
                heappush(heads, chosen)
                chosen = alternative
            previous = chosen[2]
            counts[previous] += 1
            queue = queues[previous]
            result.append(queue.popleft()[1])
            if queue:
                heappush(heads, (counts[previous], queue[0][0], previous))
    return result


def filter_key(filters):
    return hashlib.sha256(json.dumps(filters, sort_keys=True, default=str).encode()).hexdigest()


def snapshot(session, conditions, identity, *, as_of=None, after=None):
    as_of = as_of or utcnow()
    statement = select(Article.id, Article.feed_at).where(
        *conditions,
        Article.discovered_at <= as_of,
        Article.feed_at <= as_of,
        (Article.published_to_feed_at.is_(None) | (Article.published_to_feed_at <= as_of)),
    )
    if after:
        statement = statement.where(
            tuple_(Article.feed_at, Article.id)
            < tuple_(literal(datetime.fromisoformat(after[0])), literal(uuid.UUID(after[1])))
        )
    candidates = session.execute(
        statement.order_by(Article.feed_at.desc(), Article.id.desc()).limit(BATCH_SIZE + 1)
    ).all()
    rows = candidates[:BATCH_SIZE]
    if not rows:
        return {"ids": [], "after": None, "as_of": as_of.isoformat(), "filter": identity}
    publishers = dict(
        session.execute(
            select(ArticleOrigin.article_id, ArticleOrigin.source_id)
            .join(Source)
            .where(
                ArticleOrigin.article_id.in_([r.id for r in rows]),
                Source.approval_status == "approved",
            )
            .distinct(ArticleOrigin.article_id)
            .order_by(
                ArticleOrigin.article_id, Source.source_type != "publisher", ArticleOrigin.source_id
            )
        ).all()
    )
    return {
        "ids": diverse_ids(rows, publishers),
        "after": [rows[-1].feed_at.isoformat(), str(rows[-1].id)]
        if len(candidates) > BATCH_SIZE
        else None,
        "as_of": as_of.isoformat(),
        "filter": identity,
    }


def latest_page(session, conditions, filters, limit, cursor):
    identity = filter_key(filters)
    cache = get_cache()
    try:
        if cursor:
            try:
                prefix, identifier, raw_offset = cursor.split(":")
                generation = uuid.UUID(identifier)
                offset = int(raw_offset)
                if prefix != "latest-v1" or not 0 <= offset <= BATCH_SIZE:
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, "Invalid feed cursor") from exc
            stored = cache.read_sequence(f"latest-v1:{generation}", 0, 0)
            if not stored:
                raise HTTPException(409, "Latest feed expired; refresh to continue")
            state = json.loads(stored[0])
            if state["filter"] != identity or offset > len(state["ids"]):
                raise HTTPException(422, "Feed cursor does not match filters")
            if offset == len(state["ids"]) and state["after"]:
                generation, offset = uuid.uuid5(generation, "next"), 0
                key = f"latest-v1:{generation}"
                following = cache.read_sequence(key, 0, 0)
                if not following:
                    state = snapshot(
                        session,
                        conditions,
                        identity,
                        as_of=datetime.fromisoformat(state["as_of"]),
                        after=state["after"],
                    )
                    cache.write_sequence(key, [json.dumps(state).encode()], RETENTION_SECONDS)
                    following = cache.read_sequence(key, 0, 0)
                if not following:
                    raise HTTPException(409, "Latest feed expired; refresh to continue")
                state = json.loads(following[0])
        else:
            state = snapshot(session, conditions, identity)
            generation, offset = uuid.uuid4(), 0
            cache.write_sequence(
                f"latest-v1:{generation}", [json.dumps(state).encode()], RETENTION_SECONDS
            )
    except CacheUnavailable as exc:
        raise HTTPException(503, "Latest feed is temporarily unavailable") from exc
    identifiers = [uuid.UUID(value) for value in state["ids"][offset : offset + limit]]
    # Reapply visibility and filters: a snapshot must never resurrect moderated content.
    articles = (
        {
            article.id: article
            for article in session.scalars(
                select(Article)
                .options(*PUBLIC_ARTICLE_OPTIONS)
                .where(Article.id.in_(identifiers), *conditions)
            ).all()
        }
        if identifiers
        else {}
    )
    offset += len(identifiers)
    more = offset < len(state["ids"]) or state["after"] is not None
    return FeedPage(
        items=[ArticleOut.from_article(articles[i]) for i in identifiers if i in articles],
        next_cursor=f"latest-v1:{generation}:{offset}" if more else None,
    )
