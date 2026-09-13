"""Account-private saved articles. Bookmarking never changes article visibility."""

import base64
import json
import uuid

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.models import Article, ArticleBookmark
from devfeed_core.publication import visible_article
from devfeed_core.schemas import ArticleOut, FeedPage
from devfeed_http.cursors import decode_cursor
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, literal, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from devfeed_user_api.auth import User
from devfeed_user_api.dependencies import DB
from devfeed_user_api.engagement import public_article

router = APIRouter(prefix="/v1/user", tags=["bookmarks"])


class BookmarkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bookmarked: bool


class BookmarkOut(BookmarkInput):
    article_id: uuid.UUID


@router.put("/articles/{article_id}/bookmark", response_model=BookmarkOut)
def bookmark(article_id: uuid.UUID, payload: BookmarkInput, user: User, session: DB):
    user_id = uuid.UUID(user.user_id)
    if payload.bookmarked:
        public_article(session, article_id)
        session.execute(
            insert(ArticleBookmark)
            .values(article_id=article_id, user_id=user_id)
            .on_conflict_do_nothing()
        )
    else:
        # Removal remains possible after an article is withdrawn; reveal no article metadata.
        session.execute(
            delete(ArticleBookmark).where(
                ArticleBookmark.user_id == user_id, ArticleBookmark.article_id == article_id
            )
        )
    session.commit()
    return BookmarkOut(article_id=article_id, bookmarked=payload.bookmarked)


@router.get("/bookmarks", response_model=FeedPage)
def bookmarks(
    user: User,
    session: DB,
    limit: int = Query(24, ge=1, le=100),
    cursor: str | None = Query(None, max_length=300),
):
    statement = (
        select(Article, ArticleBookmark.created_at)
        .join(ArticleBookmark, ArticleBookmark.article_id == Article.id)
        .options(*PUBLIC_ARTICLE_OPTIONS)
        .where(ArticleBookmark.user_id == uuid.UUID(user.user_id), visible_article())
        .order_by(ArticleBookmark.created_at.desc(), ArticleBookmark.article_id.desc())
        .limit(limit + 1)
    )
    if cursor:
        date, identifier = decode_cursor(cursor)
        statement = statement.where(
            tuple_(ArticleBookmark.created_at, ArticleBookmark.article_id)
            < tuple_(literal(date), literal(identifier))
        )
    rows = list(session.execute(statement))
    next_cursor = None
    if len(rows) > limit:
        article, saved_at = rows[limit - 1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps([saved_at.isoformat(), str(article.id)]).encode()
        ).decode()
    return FeedPage(
        items=[ArticleOut.from_article(article) for article, _ in rows[:limit]],
        next_cursor=next_cursor,
    )
