from datetime import date, datetime, time, timedelta
from typing import Annotated
from uuid import UUID

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
from devfeed_core.read_models import ReadModelSnapshot
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, PrivateAttr
from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.automation import AutomationOverview, automation_metrics
from devfeed_admin_api.overview_insights import OverviewInsights, overview_insights
from devfeed_admin_api.reporting import reporting_sessions as session_factory

router = APIRouter(prefix="/v1/admin", tags=["admin-overview"])


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
    _reporting_fact_observation: dict | None = PrivateAttr(default=None)

    generated_at: datetime
    days: int
    snapshot: ReadModelSnapshot | None = None
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


def overview_metrics(session: Session, days: int, *, projected=False) -> AdminOverview:
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

    if getattr(session, "info", {}).get("reporting_facts") is not True:
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

    if getattr(session, "info", {}).get("reporting_facts") is True:
        from devfeed_core.models import ReportingDailyFact
        from devfeed_core.reporting_facts import TOTAL_DAY, require_reporting_facts

        require_reporting_facts(session)
        top_topics = session.execute(
            select(Topic.id, Topic.name, ReportingDailyFact.count.label("articles"))
            .join(ReportingDailyFact, ReportingDailyFact.dimension_id == Topic.id)
            .where(
                Topic.status == "active",
                ReportingDailyFact.kind == "topic.total",
                ReportingDailyFact.day == TOTAL_DAY,
                ReportingDailyFact.count > 0,
            )
            .order_by(ReportingDailyFact.count.desc(), Topic.name, Topic.id)
            .limit(5)
        ).mappings()

    if projected:
        from devfeed_core.models import ReadModelState

        from devfeed_admin_api.overview_processing import STATE_NAME

        measured = [
            job for job in insights.processing if job.kind in {"analysis", "topic-analysis"}
        ]
        analysis = tuple(
            sum(getattr(job, field) for job in measured)
            for field in ("queued", "running", "completed", "failed")
        )
        stored = session.get(ReadModelState, STATE_NAME)
        if stored is None:
            raise RuntimeError("Processing rollups are still rebuilding")
        analysis_series = {
            date.fromisoformat(day): (
                sum(
                    kinds.get(kind, {}).get("completed", 0)
                    for kind in ("analysis", "topic-analysis")
                ),
                sum(
                    kinds.get(kind, {}).get("failed", 0) for kind in ("analysis", "topic-analysis")
                ),
            )
            for day, kinds in stored.payload["daily"].items()
            if start.date().isoformat() <= day <= now.date().isoformat()
        }
    else:
        jobs = union_all(
            select(ArticleAnalysisJob.status, ArticleAnalysisJob.finished_at),
            select(TopicAnalysisJob.status, TopicAnalysisJob.finished_at),
        ).subquery()
        analysis = session.execute(
            select(
                func.count().filter(jobs.c.status == "queued"),
                func.count().filter(jobs.c.status == "running"),
                func.count().filter(
                    jobs.c.status == "succeeded",
                    jobs.c.finished_at >= start,
                    jobs.c.finished_at <= now,
                ),
                func.count().filter(
                    jobs.c.status == "failed",
                    jobs.c.finished_at >= start,
                    jobs.c.finished_at <= now,
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
    automation = automation_metrics(session, start, now, projected=projected)
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
    response: Response,
    admin: Admin,
    sessions: Annotated[sessionmaker[Session], Depends(session_factory)],
    days: int = Query(default=30, ge=1, le=90),
):
    from devfeed_admin_api.overview_projection import read_report

    result = await run_in_threadpool(read_report, sessions, "overview", days, AdminOverview)
    if result.snapshot is not None and result.snapshot.status != "fresh":
        response.headers["X-Overview-Stale"] = "true"
        response.headers["X-Overview-Age-Seconds"] = str(int(result.snapshot.lag_seconds))
    return result


async def close_snapshot_tasks(app):
    # Report publication belongs to the dedicated background worker.
    pass
