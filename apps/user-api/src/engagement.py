"""First-party article interactions; no IP addresses or browser fingerprints stored."""

import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Annotated

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
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from starlette.concurrency import run_in_threadpool

from devfeed_user_api import oidc
from devfeed_user_api.auth import TOKEN, User, UserIdentity, require_user
from devfeed_user_api.config import get_settings
from devfeed_user_api.dependencies import DB
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


class EngagementOut(BaseModel):
    article_id: uuid.UUID
    likes: int
    opens: int
    liked: bool
    bookmarked: bool = False


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
    return [dict(row) for row in session.execute(statement).mappings()]


async def _with_opens(rows, analytics, cutover):
    totals = await analytics.totals([row["article_id"] for row in rows], cutover=cutover)
    return [EngagementOut(**row, opens=totals[str(row["article_id"])]) for row in rows]


def public_article(session, article_id: uuid.UUID) -> None:
    if (
        session.scalar(select(Article.id).where(Article.id == article_id, visible_article()))
        is None
    ):
        raise HTTPException(404, "Article not found")


@router.get("/engagement", response_model=list[EngagementOut])
async def engagement(
    session: DB,
    viewer: Viewer,
    client: ClickHouse,
    article_id: Annotated[list[uuid.UUID], Query(min_length=1, max_length=100)],
):
    analytics = ArticleOpenAnalytics(client)
    cutover = await analytics.cutover()
    rows = await run_in_threadpool(engagement_rows, session, article_id, viewer)
    return await _with_opens(rows, analytics, cutover)


def _write_like(article_id, payload, user, session):
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
    article_id: uuid.UUID, payload: LikeInput, user: User, session: DB, client: ClickHouse
):
    analytics = ArticleOpenAnalytics(client)
    cutover = await analytics.cutover()
    rows = await run_in_threadpool(_write_like, article_id, payload, user, session)
    return (await _with_opens(rows, analytics, cutover))[0]


def _authorize_open(session, article_id, viewer_key, anonymous):
    limit_open_requests(article_id, viewer_key, anonymous=anonymous)
    public_article(session, article_id)


def _record_reading_day(session, viewer, article_id, opened_at):
    from devfeed_user_api.preferences import lock_account

    account = lock_account(session, viewer)
    record_reading_day(session, account, article_id, opened_at)
    session.commit()


def _set_visitor_cookie(response, name, token, settings):
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
    session: DB,
    client: ClickHouse,
):
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
    await run_in_threadpool(_authorize_open, session, article_id, viewer_key, viewer is None)
    analytics = ArticleOpenAnalytics(client)
    cutover = await analytics.write_cutover()
    opened_at = utcnow()
    if viewer:
        await run_in_threadpool(_record_reading_day, session, viewer, article_id, opened_at)
    try:
        await analytics.record(article_id, viewer_key, opened_at)
        await analytics.cutover()
        rows = await run_in_threadpool(engagement_rows, session, [article_id], viewer)
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
    session: DB,
    client: ClickHouse,
    limit: int = Query(24, ge=1, le=100),
    cursor: int = Query(0, ge=0, le=1_000_000),
):
    analytics = ArticleOpenAnalytics(client)
    await analytics.cutover()
    return await trending_page(
        session, analytics, since=utcnow() - timedelta(days=7), limit=limit, cursor=cursor
    )
