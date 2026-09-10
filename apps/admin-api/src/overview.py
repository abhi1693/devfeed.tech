from datetime import date, datetime, time, timedelta
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
from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.automation import AutomationOverview, automation_metrics
from devfeed_admin_api.dependencies import DB

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
    automation: AutomationOverview | None = None


def overview_metrics(session: Session, days: int) -> AdminOverview:
    """Current inventory plus daily activity, bounded to a small UTC date range.

    Publication activity counts each article's first publication, including articles
    subsequently unpublished. It is distinct from the currently published inventory.
    AI completions use finished_at; the queue includes jobs created before the range.
    """
    now = utcnow()
    start = datetime.combine(now.date() - timedelta(days=days - 1), time.min, tzinfo=now.tzinfo)
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

    series: list[dict[date, int]] = []
    for timestamp in (Article.discovered_at, Article.published_to_feed_at):
        day = func.date(func.timezone("UTC", timestamp))
        series.append(
            {
                bucket: count
                for bucket, count in session.execute(
                    select(day, func.count(Article.id))
                    .where(timestamp >= start, timestamp <= now)
                    .group_by(day)
                )
            }
        )

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
    return AdminOverview(
        automation=automation_metrics(session, start, now),
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
            OverviewActivity(date=day, added=series[0].get(day, 0), published=series[1].get(day, 0))
            for day in (start.date() + timedelta(days=offset) for offset in range(days))
        ],
        top_topics=[OverviewTopic.model_validate(row) for row in top_topics],
        analysis=OverviewAnalysis(
            queued=analysis[0], running=analysis[1], succeeded=analysis[2], failed=analysis[3]
        ),
    )


@router.get("/overview", response_model=AdminOverview, operation_id="admin_overview")
def overview(admin: Admin, session: DB, days: int = Query(default=30, ge=1, le=90)):
    return overview_metrics(session, days)
