import asyncio
import time as clock
from contextlib import suppress
from datetime import date, datetime, time, timedelta
from typing import Annotated
from uuid import UUID

import anyio
from devfeed_core.cache import CacheUnavailable, get_cache
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleTopic,
    Source,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelationProposal,
    utcnow,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.automation import AutomationOverview, automation_metrics
from devfeed_admin_api.overview_insights import OverviewInsights, overview_insights

router = APIRouter(prefix="/v1/admin", tags=["admin-overview"])
REFRESH_WAIT_SECONDS = 5.0
REFRESH_POLL_SECONDS = 0.05


class OverviewActivity(BaseModel):
    date: date
    added: int
    published: int


class OverviewTopic(BaseModel):
    id: UUID
    name: str
    articles: int


class OverviewAnalysis(BaseModel):
    queued: int
    running: int
    succeeded: int
    failed: int


class OverviewAnalysisActivity(BaseModel):
    date: date
    succeeded: int
    failed: int


class AdminOverview(BaseModel):
    generated_at: datetime
    days: int
    articles: int
    articles_pending_review: int
    articles_published: int
    sources: int
    sources_pending_review: int
    sources_active: int
    sources_failing: int
    topics: int
    topics_active: int
    topic_proposals_pending: int
    relationship_proposals_pending: int
    activity: list[OverviewActivity]
    top_topics: list[OverviewTopic]
    analysis: OverviewAnalysis
    analysis_activity: list[OverviewAnalysisActivity] = Field(default_factory=list)
    automation: AutomationOverview | None = None
    insights: OverviewInsights = Field(default_factory=OverviewInsights)


def overview_metrics(session: Session, days: int) -> AdminOverview:
    """Current inventory plus daily activity, bounded to a small UTC date range.

    Publication activity counts each article's first publication, including articles
    subsequently unpublished. It is distinct from the currently published inventory.
    AI completions use finished_at; the queue includes jobs created before the range.
    """
    now = utcnow()
    start = datetime.combine(now.date() - timedelta(days=days - 1), time.min, tzinfo=now.tzinfo)
    insights = overview_insights(session, days, now)
    article = session.execute(
        select(
            func.count(Article.id),
            func.count(Article.id).filter(Article.review_status == "pending"),
            func.count(Article.id).filter(Article.publication_status == "published"),
        )
    ).one()
    source = session.execute(
        select(
            func.count(Source.id),
            func.count(Source.id).filter(Source.approval_status == "pending"),
            func.count(Source.id).filter(Source.approval_status == "approved", Source.enabled),
            func.count(Source.id).filter(
                Source.approval_status == "approved",
                Source.enabled,
                Source.consecutive_failures > 0,
            ),
        )
    ).one()
    taxonomy = session.execute(
        select(
            select(func.count(Topic.id)).scalar_subquery(),
            select(func.count(Topic.id)).where(Topic.status == "active").scalar_subquery(),
            select(func.count(TopicProposal.id))
            .where(TopicProposal.status == "pending")
            .scalar_subquery(),
            select(func.count(TopicRelationProposal.id))
            .where(TopicRelationProposal.status == "pending")
            .scalar_subquery(),
        )
    ).one()

    published_count = func.count(ArticleTopic.article_id)
    top_topics = session.execute(
        select(Topic.id, Topic.name, published_count.label("articles"))
        .join(ArticleTopic, ArticleTopic.topic_id == Topic.id)
        .join(Article, Article.id == ArticleTopic.article_id)
        .where(
            Topic.status == "active",
            Article.publication_status == "published",
            ArticleTopic.role.in_(["primary", "supporting"]),
        )
        .group_by(Topic.id, Topic.name)
        .order_by(published_count.desc(), Topic.name, Topic.id)
        .limit(5)
    ).mappings()

    jobs = union_all(
        select(ArticleAnalysisJob.status, ArticleAnalysisJob.finished_at),
        select(TopicAnalysisJob.status, TopicAnalysisJob.finished_at),
    ).subquery()
    analysis = session.execute(
        select(
            func.count().filter(jobs.c.status == "queued"),
            func.count().filter(jobs.c.status == "running"),
            func.count().filter(
                jobs.c.status == "succeeded", jobs.c.finished_at >= start, jobs.c.finished_at <= now
            ),
            func.count().filter(
                jobs.c.status == "failed", jobs.c.finished_at >= start, jobs.c.finished_at <= now
            ),
        ).select_from(jobs)
    ).one()
    analysis_day = func.date(func.timezone("UTC", jobs.c.finished_at))
    analysis_series = {
        day: (succeeded, failed)
        for day, succeeded, failed in session.execute(
            select(
                analysis_day,
                func.count().filter(jobs.c.status == "succeeded"),
                func.count().filter(jobs.c.status == "failed"),
            )
            .where(jobs.c.finished_at >= start, jobs.c.finished_at <= now)
            .group_by(analysis_day)
        )
    }
    automation = automation_metrics(session, start, now)
    return AdminOverview(
        automation=automation,
        insights=insights,
        generated_at=now,
        days=days,
        articles=article[0],
        articles_pending_review=article[1],
        articles_published=article[2],
        sources=source[0],
        sources_pending_review=source[1],
        sources_active=source[2],
        sources_failing=source[3],
        topics=taxonomy[0],
        topics_active=taxonomy[1],
        topic_proposals_pending=taxonomy[2],
        relationship_proposals_pending=taxonomy[3],
        activity=[
            OverviewActivity(date=day.date, added=day.added, published=day.published)
            for day in insights.reader_activity
        ],
        top_topics=[OverviewTopic.model_validate(row) for row in top_topics],
        analysis=OverviewAnalysis(
            queued=analysis[0], running=analysis[1], succeeded=analysis[2], failed=analysis[3]
        ),
        analysis_activity=[
            OverviewAnalysisActivity(
                date=day,
                succeeded=analysis_series.get(day, (0, 0))[0],
                failed=analysis_series.get(day, (0, 0))[1],
            )
            for day in (start.date() + timedelta(days=offset) for offset in range(days))
        ],
    )


@router.get("/overview", response_model=AdminOverview, operation_id="admin_overview")
async def overview(
    request: Request,
    admin: Admin,
    sessions: Annotated[sessionmaker[Session], Depends(session_factory)],
    days: int = Query(default=30, ge=1, le=90),
):
    # Authentication is evaluated before cache lookup. No browser/shared HTTP caching.
    if not get_settings().cache_enabled:
        return await run_in_threadpool(load_overview, sessions, days)
    deadline = clock.monotonic() + REFRESH_WAIT_SECONDS
    # At most 90 locks per app, one per validated day range. Local waiters share
    # one Redis poller instead of exhausting its pool and falling back to SQL.
    lock = request.app.state.overview_locks.setdefault(days, asyncio.Lock())
    try:
        await asyncio.wait_for(lock.acquire(), timeout=REFRESH_WAIT_SECONDS)
    except TimeoutError:
        raise refreshing() from None
    try:
        return await cached_overview(sessions, days, deadline)
    finally:
        lock.release()


def refreshing() -> HTTPException:
    return HTTPException(
        503, "Overview is refreshing. Try again shortly.", headers={"Retry-After": "2"}
    )


async def cached_overview(sessions: sessionmaker[Session], days: int, deadline: float):
    cache = await run_in_threadpool(get_cache)
    try:
        while True:
            lookup = await run_in_threadpool(
                cache.lookup, f"admin-overview-v1:{days}", "admin-overview"
            )
            if lookup.body is not None:
                try:
                    return AdminOverview.model_validate_json(lookup.body)
                except ValueError:
                    return await run_in_threadpool(load_overview, sessions, days)
            if lookup.token is not None:
                break
            remaining = deadline - clock.monotonic()
            if remaining <= 0:
                raise refreshing()
            # Wait without occupying a request thread or database connection.
            await anyio.sleep(min(REFRESH_POLL_SECONDS, remaining))
    except CacheUnavailable:
        return await run_in_threadpool(load_overview, sessions, days)
    try:
        result = await run_in_threadpool(load_overview, sessions, days)
        with suppress(CacheUnavailable):
            await run_in_threadpool(cache.publish, lookup, result.model_dump_json().encode(), 60)
        return result
    finally:
        with suppress(CacheUnavailable):
            await run_in_threadpool(cache.release, lookup)


def load_overview(sessions: sessionmaker[Session], days: int) -> AdminOverview:
    # The synchronous session is created, used and closed in one worker thread.
    # Return its connection before Redis publication and response serialization.
    with sessions() as session:
        return overview_metrics(session, days)
