"""First-party article interactions; no IP addresses or browser fingerprints stored."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Annotated, TypedDict

from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.analytics_article_opens import ArticleOpenAnalytics
from devfeed_core.models import (
    Article,
    ArticleBookmark,
    ArticleLike,
    utcnow,
)
from devfeed_core.publication import visible_article
from devfeed_core.reading_streaks import record_reading_day
from devfeed_core.schemas import FeedPage
from devfeed_http.agent_permissions import AgentAccessPolicy
from devfeed_http.analytics import analytics_client
from devfeed_http.dependencies import run_database
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from devfeed_user_api import oidc
from devfeed_user_api.auth import TOKEN, User, UserIdentity, require_user
from devfeed_user_api.config import Settings, get_settings
from devfeed_user_api.dependencies import DBFactory, ReadDBFactory
from devfeed_user_api.open_limits import limit_open_requests
from devfeed_user_api.trending import trending_page

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
ClickHouse = Annotated[AnalyticsClient, Depends(analytics_client)]


class EngagementRow(TypedDict):
    article_id: uuid.UUID
    likes: int
    liked: bool
    bookmarked: bool


class EngagementOut(BaseModel):
    article_id: uuid.UUID
    likes: int
    opens: int
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


async def _with_opens(
    rows: Sequence[EngagementRow], analytics: ArticleOpenAnalytics, cutover: datetime
) -> list[EngagementOut]:
    totals = await analytics.totals([row["article_id"] for row in rows], cutover=cutover)
    return [EngagementOut(**row, opens=totals[str(row["article_id"])]) for row in rows]


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
    client: ClickHouse,
    article_id: Annotated[list[uuid.UUID], Query(min_length=1, max_length=100)],
) -> list[EngagementOut]:
    analytics = ArticleOpenAnalytics(client)
    cutover = await analytics.cutover()
    rows = await run_database(factory, engagement_rows, article_id, viewer)
    return await _with_opens(rows, analytics, cutover)


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
    article_id: uuid.UUID, payload: LikeInput, user: User, factory: DBFactory, client: ClickHouse
) -> EngagementOut:
    analytics = ArticleOpenAnalytics(client)
    cutover = await analytics.cutover()
    rows = await run_database(factory, _write_like, article_id, payload, user)
    return (await _with_opens(rows, analytics, cutover))[0]


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


def _set_visitor_cookie(response: Response, name: str, token: str, settings: Settings) -> None:
    response.set_cookie(
        name,
        token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/articles/{article_id}/open", response_model=EngagementOut)
async def opened(
    article_id: uuid.UUID,
    request: Request,
    response: Response,
    viewer: Viewer,
    factory: DBFactory,
    client: ClickHouse,
) -> EngagementOut | JSONResponse:
    settings = get_settings()
    if not settings.base_url or not settings.allows_request_origin(request.headers.get("origin")):
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
    await run_database(factory, _authorize_open, article_id, viewer_key, viewer is None)
    analytics = ArticleOpenAnalytics(client)
    cutover = await analytics.write_cutover()
    opened_at = utcnow()
    if viewer:
        await run_database(factory, _record_reading_day, viewer, article_id, opened_at)
    try:
        await analytics.record(article_id, viewer_key, opened_at)
        await analytics.cutover()
        rows = await run_database(factory, engagement_rows, [article_id], viewer)
        result = (await _with_opens(rows, analytics, cutover))[0]
    except AnalyticsUnavailable:
        # Preserve the newly assigned anonymous identity even if the insert was
        # acknowledged after a network timeout, so retry does not double count.
        failure = JSONResponse(status_code=503, content={"detail": "Analytics unavailable"})
        if new_visitor and not viewer:
            _set_visitor_cookie(failure, cookie_name, token, settings)
        return failure
    if new_visitor and not viewer:
        _set_visitor_cookie(response, cookie_name, token, settings)
    return result


@router.get("/trending", response_model=FeedPage)
async def trending(
    factory: ReadDBFactory,
    client: ClickHouse,
    limit: int = Query(24, ge=1, le=100),
    cursor: int = Query(0, ge=0, le=1_000_000),
) -> FeedPage:
    analytics = ArticleOpenAnalytics(client)
    await analytics.cutover()
    return await trending_page(
        factory, analytics, since=utcnow() - timedelta(days=7), limit=limit, cursor=cursor
    )
