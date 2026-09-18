"""Keyset pagination for chronological ascending and most-liked public feeds."""

import base64
import binascii
import hashlib
import json
import uuid
from datetime import datetime

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.models import Article, ArticleLike
from devfeed_core.schemas import ArticleOut, FeedPage
from fastapi import HTTPException
from sqlalchemy import func, literal, select, tuple_


def ordered_page(session, conditions, filters, sort, limit, cursor):
    scope = hashlib.sha256(
        json.dumps([sort, filters], sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    likes = (
        select(func.count())
        .where(ArticleLike.article_id == Article.id)
        .correlate(Article)
        .scalar_subquery()
    )
    score = likes if sort == "most_liked" else literal(0)
    if cursor:
        try:
            if not cursor.startswith("sorted-v1:"):
                raise ValueError("Wrong cursor type")
            signature, count, timestamp, identity = json.loads(
                base64.b64decode(cursor[10:], altchars=b"-_", validate=True)
            )
            date = datetime.fromisoformat(timestamp)
            identifier = uuid.UUID(identity)
            if signature != scope or type(count) is not int or count < 0 or date.tzinfo is None:
                raise ValueError("Wrong cursor scope")
        except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
            raise HTTPException(422, "Invalid feed cursor") from exc
        if sort == "oldest":
            conditions.append(
                tuple_(Article.feed_at, Article.id) > tuple_(literal(date), literal(identifier))
            )
        else:
            conditions.append(
                tuple_(score, Article.feed_at, Article.id)
                < tuple_(literal(count), literal(date), literal(identifier))
            )
    order = (
        (Article.feed_at.asc(), Article.id.asc())
        if sort == "oldest"
        else (score.desc(), Article.feed_at.desc(), Article.id.desc())
    )
    page = (
        select(Article.id, score.label("likes"))
        .where(*conditions)
        .order_by(*order)
        .limit(limit + 1)
        .cte("ordered_page")
        .prefix_with("MATERIALIZED")
    )
    rows = session.execute(
        select(Article, page.c.likes)
        .join(page, page.c.id == Article.id)
        .options(*PUBLIC_ARTICLE_OPTIONS)
        .order_by(
            *(
                (Article.feed_at.asc(), Article.id.asc())
                if sort == "oldest"
                else (page.c.likes.desc(), Article.feed_at.desc(), Article.id.desc())
            )
        )
    ).all()
    selected = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        article, count = selected[-1]
        next_cursor = (
            "sorted-v1:"
            + base64.urlsafe_b64encode(
                json.dumps([scope, count, article.feed_at.isoformat(), str(article.id)]).encode()
            ).decode()
        )
    return FeedPage(
        items=[ArticleOut.from_article(article) for article, _ in selected], next_cursor=next_cursor
    )
