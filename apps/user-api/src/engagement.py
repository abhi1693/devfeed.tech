"""First-party article interactions; no IP addresses or browser fingerprints stored."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Annotated, TypedDict

from devfeed_core.models import (
    Article,
    ArticleBookmark,
    ArticleLike,
    utcnow,
)
from devfeed_core.publication import visible_article
from devfeed_core.reading_streaks import record_reading_day
from devfeed_http.agent_permissions import AgentAccessPolicy
from devfeed_http.dependencies import run_database
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from devfeed_user_api import oidc
from devfeed_user_api.auth import TOKEN, User, UserIdentity, require_user
from devfeed_user_api.config import get_settings
from devfeed_user_api.dependencies import DBFactory
from devfeed_user_api.open_limits import limit_open_requests

router = APIRouter(prefix="/v1/user", tags=["article-engagement"])


def optional_user(request: Request) -> UserIdentity | None:
    settings = get_settings()
    token = request.cookies.get(oidc.cookie_name(settings, "session"), "")
    if not TOKEN.fullmatch(token):
        return None
    try:
        return require_user(request)
    except HTTPException as exc:
        # Expired/revoked sessions are anonymous, just like the /auth/me probe.
        # Keep CSRF failures and dependency outages explicit; never grant a user
        # identity or bypass the shared anonymous tracking budgets on failure.
        if exc.status_code == 401:
            return None
        raise


Viewer = Annotated[UserIdentity | None, Depends(optional_user)]


class EngagementRow(TypedDict):
    article_id: uuid.UUID
    likes: int
    liked: bool
    bookmarked: bool


class EngagementOut(BaseModel):
    article_id: uuid.UUID
    likes: int
    liked: bool
    bookmarked: bool = False


class LikeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    liked: bool


def engagement_rows(
    session: Session, article_ids: list[uuid.UUID], user: UserIdentity | None
) -> list[EngagementRow]:
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
    bookmarked = (
        select(1)
        .where(
            ArticleBookmark.article_id == Article.id,
            ArticleBookmark.user_id == uuid.UUID(user.user_id),
        )
        .exists()
        if user
        else literal(False)
    )
    statement = select(
        Article.id.label("article_id"),
        likes.label("likes"),
        liked.label("liked"),
        bookmarked.label("bookmarked"),
    ).where(Article.id.in_(article_ids), visible_article())
    return [
        {
            "article_id": row["article_id"],
            "likes": row["likes"],
            "liked": row["liked"],
            "bookmarked": row["bookmarked"],
        }
        for row in session.execute(statement).mappings()
    ]


def public_article(session: Session, article_id: uuid.UUID) -> None:
    if (
        session.scalar(select(Article.id).where(Article.id == article_id, visible_article()))
        is None
    ):
        raise HTTPException(404, "Article not found")


@router.get("/engagement", response_model=list[EngagementOut])
async def engagement(
    factory: DBFactory,
    viewer: Viewer,
    article_id: Annotated[list[uuid.UUID], Query(min_length=1, max_length=100)],
) -> list[EngagementOut]:
    rows = await run_database(factory, engagement_rows, article_id, viewer)
    return [EngagementOut(**row) for row in rows]


def _write_like(
    session: Session, article_id: uuid.UUID, payload: LikeInput, user: UserIdentity
) -> list[EngagementRow]:
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
    return engagement_rows(session, [article_id], user)


@router.put(
    "/articles/{article_id}/like",
    response_model=EngagementOut,
    openapi_extra=AgentAccessPolicy(scope="devfeed:write").openapi_extra(),
)
async def like(
    article_id: uuid.UUID, payload: LikeInput, user: User, factory: DBFactory
) -> EngagementOut:
    rows = await run_database(factory, _write_like, article_id, payload, user)
    return EngagementOut(**rows[0])


def _authorize_open(
    session: Session, article_id: uuid.UUID, viewer_key: str, anonymous: bool
) -> None:
    limit_open_requests(article_id, viewer_key, anonymous=anonymous)
    public_article(session, article_id)


def _record_reading_day(
    session: Session, viewer: UserIdentity, article_id: uuid.UUID, opened_at: datetime
) -> None:
    from devfeed_user_api.preferences import lock_account

    account = lock_account(session, viewer)
    record_reading_day(session, account, article_id, opened_at)
    session.commit()


@router.post("/articles/{article_id}/open", response_model=EngagementOut)
async def opened(
    article_id: uuid.UUID, request: Request, viewer: User, factory: DBFactory
) -> EngagementOut:
    settings = get_settings()
    if not settings.base_url or not settings.allows_request_origin(request.headers.get("origin")):
        raise HTTPException(403, "Invalid request origin")
    viewer_key = hashlib.sha256(("user:" + viewer.user_id).encode()).hexdigest()
    await run_database(factory, _authorize_open, article_id, viewer_key, False)
    await run_database(factory, _record_reading_day, viewer, article_id, utcnow())
    rows = await run_database(factory, engagement_rows, [article_id], viewer)
    return EngagementOut(**rows[0])
