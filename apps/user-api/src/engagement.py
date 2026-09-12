"""First-party article interactions; no IP addresses or browser fingerprints stored."""

import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Annotated

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.models import Article, ArticleEngagement, ArticleLike, ArticleOpen, utcnow
from devfeed_core.publication import visible_article
from devfeed_core.schemas import ArticleOut, FeedPage
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, literal, select, union_all
from sqlalchemy.dialects.postgresql import insert

from devfeed_user_api import oidc
from devfeed_user_api.auth import TOKEN, User, UserIdentity, require_user
from devfeed_user_api.config import get_settings
from devfeed_user_api.dependencies import DB
from devfeed_user_api.open_limits import limit_open_requests

router = APIRouter(prefix="/v1/user", tags=["article-engagement"])


def optional_user(request: Request) -> UserIdentity | None:
    settings = get_settings()
    if not request.cookies.get(oidc.cookie_name(settings, "session")):
        return None
    return require_user(request)


Viewer = Annotated[UserIdentity | None, Depends(optional_user)]


class EngagementOut(BaseModel):
    article_id: uuid.UUID
    likes: int
    opens: int
    liked: bool


class LikeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    liked: bool


def engagement_rows(session, article_ids: list[uuid.UUID], user: UserIdentity | None):
    likes = (
        select(func.count())
        .select_from(ArticleLike)
        .where(ArticleLike.article_id == Article.id)
        .scalar_subquery()
    )
    liked = (
        select(1)
        .where(
            ArticleLike.article_id == Article.id,
            ArticleLike.user_id == uuid.UUID(user.user_id),
        )
        .exists()
        if user
        else literal(False)
    )
    statement = (
        select(
            Article.id.label("article_id"),
            likes.label("likes"),
            func.coalesce(ArticleEngagement.opens, 0).label("opens"),
            liked.label("liked"),
        )
        .outerjoin(ArticleEngagement, ArticleEngagement.article_id == Article.id)
        .where(Article.id.in_(article_ids), visible_article())
    )
    return [EngagementOut(**row) for row in session.execute(statement).mappings()]


def public_article(session, article_id: uuid.UUID) -> None:
    if (
        session.scalar(select(Article.id).where(Article.id == article_id, visible_article()))
        is None
    ):
        raise HTTPException(404, "Article not found")


@router.get("/engagement", response_model=list[EngagementOut])
def engagement(
    session: DB,
    viewer: Viewer,
    article_id: Annotated[list[uuid.UUID], Query(min_length=1, max_length=100)],
):
    return engagement_rows(session, article_id, viewer)


@router.put("/articles/{article_id}/like", response_model=EngagementOut)
def like(article_id: uuid.UUID, payload: LikeInput, user: User, session: DB):
    public_article(session, article_id)
    values = {"article_id": article_id, "user_id": uuid.UUID(user.user_id)}
    if payload.liked:
        session.execute(insert(ArticleLike).values(**values).on_conflict_do_nothing())
    else:
        session.execute(
            delete(ArticleLike).where(
                ArticleLike.article_id == article_id, ArticleLike.user_id == values["user_id"]
            )
        )
    session.commit()
    return engagement_rows(session, [article_id], user)[0]


@router.post("/articles/{article_id}/open", response_model=EngagementOut)
def opened(
    article_id: uuid.UUID, request: Request, response: Response, viewer: Viewer, session: DB
):
    settings = get_settings()
    if not settings.base_url or request.headers.get("origin") != settings.base_url.rstrip("/"):
        raise HTTPException(403, "Invalid request origin")
    cookie_name = oidc.cookie_name(settings, "visitor")
    token = request.cookies.get(cookie_name, "")
    new_visitor = not TOKEN.fullmatch(token)
    if new_visitor:
        token = secrets.token_urlsafe(32)
    # User identity is stable across devices; anonymous identity is an opaque
    # first-party cookie. Never count prefetches or disclose individual viewers.
    identity = "user:" + viewer.user_id if viewer else "visitor:" + token
    viewer_key = hashlib.sha256(identity.encode()).hexdigest()
    limit_open_requests(article_id, viewer_key, anonymous=viewer is None)
    public_article(session, article_id)
    hour = utcnow().replace(minute=0, second=0, microsecond=0)
    recorded = session.scalar(
        insert(ArticleOpen)
        .values(
            article_id=article_id,
            viewer_key=viewer_key,
            opened_hour=hour,
        )
        .on_conflict_do_nothing()
        .returning(ArticleOpen.article_id)
    )
    if recorded:
        session.execute(
            insert(ArticleEngagement)
            .values(article_id=article_id, opens=1)
            .on_conflict_do_update(
                index_elements=[ArticleEngagement.article_id],
                set_={"opens": ArticleEngagement.opens + 1},
            )
        )
    session.commit()
    if new_visitor and not viewer:
        response.set_cookie(
            cookie_name,
            token,
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/",
        )
    return engagement_rows(session, [article_id], viewer)[0]


@router.get("/trending", response_model=FeedPage)
def trending(
    session: DB,
    limit: int = Query(24, ge=1, le=100),
    cursor: int = Query(0, ge=0, le=1_000_000),
):
    # Recent likes are a stronger signal than an open. No invented popularity:
    # only public articles with real recorded activity can enter this list.
    since = utcnow() - timedelta(days=7)
    activity = union_all(
        select(ArticleOpen.article_id, literal(1).label("weight")).where(
            ArticleOpen.opened_hour >= since
        ),
        select(ArticleLike.article_id, literal(3).label("weight")).where(
            ArticleLike.created_at >= since
        ),
    ).subquery()
    scores = (
        select(activity.c.article_id, func.sum(activity.c.weight).label("score"))
        .group_by(activity.c.article_id)
        .subquery()
    )
    statement = (
        select(Article)
        .options(*PUBLIC_ARTICLE_OPTIONS)
        .join(scores, scores.c.article_id == Article.id)
        .where(visible_article())
        .order_by(scores.c.score.desc(), Article.feed_at.desc(), Article.id.desc())
        .offset(cursor)
        .limit(limit + 1)
    )
    articles = list(session.scalars(statement))
    return FeedPage(
        items=[ArticleOut.from_article(article) for article in articles[:limit]],
        next_cursor=str(cursor + limit)
        if len(articles) > limit and cursor + limit <= 1_000_000
        else None,
    )
