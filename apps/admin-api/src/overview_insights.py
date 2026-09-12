"""Reader, publication and personalization aggregates for the private overview."""

from datetime import datetime, time, timedelta
from uuid import UUID

from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.models import (
    Article,
    ArticleLike,
    ArticleOpen,
    ArticleOrigin,
    ArticleTopic,
    IngestionJob,
    Source,
    Topic,
    TopicProposal,
    TopicRelationProposal,
    UserAccount,
    UserInterest,
    UserRecommendation,
    UserRecommendationState,
    UserSource,
    UserTopic,
)
from devfeed_core.overview_daily import read_daily_metrics
from devfeed_core.recommendations import has_recommendation_work
from pydantic import BaseModel, Field
from sqlalchemy import case, func, literal, select, union_all


class OverviewMetric(BaseModel):
    current: float | None = None
    previous: float | None = None


class OverviewReaderDay(BaseModel):
    date: str
    added: int = 0
    published: int = 0
    accounts: int = 0
    opens: int | None = None
    content_types: dict[str, int] = Field(default_factory=dict)
    median_publication_seconds: float | None = None


class OverviewPopularArticle(BaseModel):
    id: UUID
    title: str
    opens: int
    likes: int


class OverviewInterestCoverage(BaseModel):
    id: UUID
    name: str
    logo_url: str | None
    followers: int
    inferred_users: int
    publications: int
    last_published_at: datetime | None


class OverviewSourcePerformance(BaseModel):
    id: UUID
    name: str
    logo_url: str | None
    discovered: int
    published: int
    followers: int
    fetch_failures: int
    consecutive_failures: int
    last_success_at: datetime | None


class OverviewUserIssue(BaseModel):
    id: UUID
    name: str
    issue: str
    next_refresh_at: datetime | None


class OverviewReason(BaseModel):
    reason: str
    users: int
    recommendations: int


class OverviewPersonalization(BaseModel):
    ready: int = 0
    refreshing: int = 0
    expired: int = 0
    pending: int = 0
    not_needed: int = 0
    overdue: int = 0
    empty_with_interests: int = 0
    issues: list[OverviewUserIssue] = Field(default_factory=list)
    reasons: list[OverviewReason] = Field(default_factory=list)


class OverviewProcessing(BaseModel):
    kind: str
    queued: int
    running: int
    completed: int
    failed: int
    oldest_queued_at: datetime | None
    duration_ms: int = 0
    duration_runs: int = 0


class OverviewInsights(BaseModel):
    publications: OverviewMetric = Field(default_factory=OverviewMetric)
    opens: OverviewMetric = Field(default_factory=OverviewMetric)
    accounts: OverviewMetric = Field(default_factory=OverviewMetric)
    publication_seconds: OverviewMetric = Field(default_factory=OverviewMetric)
    publication_p90_seconds: float | None = None
    reader_activity: list[OverviewReaderDay] = Field(default_factory=list)
    top_articles: list[OverviewPopularArticle] = Field(default_factory=list)
    top_articles_days: int = 30
    coverage: list[OverviewInterestCoverage] = Field(default_factory=list)
    source_performance: list[OverviewSourcePerformance] = Field(default_factory=list)
    failing_sources: list[OverviewSourcePerformance] = Field(default_factory=list)
    personalization: OverviewPersonalization = Field(default_factory=OverviewPersonalization)
    processing: list[OverviewProcessing] = Field(default_factory=list)
    oldest_review_at: dict[str, datetime | None] = Field(default_factory=dict)
    analysis_average_seconds: float | None = None


def personalization(session, now):
    state = UserRecommendationState
    work = has_recommendation_work(UserAccount.id)
    recs = (
        select(UserRecommendation.user_id)
        .where(UserRecommendation.user_id == UserAccount.id)
        .exists()
    )
    inputs = (
        select(UserTopic.user_id).where(UserTopic.user_id == UserAccount.id).exists()
        | select(UserSource.user_id).where(UserSource.user_id == UserAccount.id).exists()
        | select(ArticleLike.user_id).where(ArticleLike.user_id == UserAccount.id).exists()
    )
    status = case(
        (~work, "not_needed"),
        (state.generation.is_(None), "pending"),
        (state.invalidated, "refreshing"),
        ((state.expires_at.is_(None)) | (state.expires_at <= now), "expired"),
        else_="ready",
    )
    rows = (
        select(
            UserAccount.id,
            func.coalesce(
                UserAccount.profile["display_name"].astext, UserAccount.name, "Unnamed user"
            ).label("name"),
            status.label("status"),
            state.next_refresh_at,
            (work & (state.next_refresh_at < now - timedelta(minutes=15))).label("overdue"),
            (inputs & ~recs & (status == "ready")).label("empty"),
        )
        .outerjoin(state, state.user_id == UserAccount.id)
        .subquery()
    )
    counts = session.execute(
        select(
            *[
                func.count().filter(rows.c.status == value)
                for value in ("ready", "refreshing", "expired", "pending", "not_needed")
            ],
            func.count().filter(rows.c.overdue),
            func.count().filter(rows.c.empty),
        ).select_from(rows)
    ).one()
    issues = session.execute(
        select(rows)
        .where(rows.c.overdue | rows.c.empty | (rows.c.status == "expired"))
        .order_by(rows.c.overdue.desc(), rows.c.next_refresh_at.asc().nullsfirst(), rows.c.id)
        .limit(5)
    ).mappings()
    reasons = session.execute(
        select(
            UserRecommendation.reason,
            func.count(func.distinct(UserRecommendation.user_id)).label("users"),
            func.count().label("recommendations"),
        )
        .group_by(UserRecommendation.reason)
        .order_by(func.count().desc())
    ).mappings()
    return OverviewPersonalization(
        **dict(
            zip(
                (
                    "ready",
                    "refreshing",
                    "expired",
                    "pending",
                    "not_needed",
                    "overdue",
                    "empty_with_interests",
                ),
                counts,
                strict=True,
            )
        ),
        issues=[
            OverviewUserIssue(
                id=r["id"],
                name=r["name"],
                issue="Refresh overdue"
                if r["overdue"]
                else "Expired feed"
                if r["status"] == "expired"
                else "No prepared recommendations",
                next_refresh_at=r["next_refresh_at"],
            )
            for r in issues
        ],
        reasons=[OverviewReason(**r) for r in reasons],
    )


def interest_coverage(session, start, now):
    follows = (
        select(UserTopic.topic_id, func.count().label("followers"))
        .group_by(UserTopic.topic_id)
        .subquery()
    )
    inferred = (
        select(UserInterest.topic_id, func.count().label("users"))
        .where(UserInterest.reason == "related_topic")
        .group_by(UserInterest.topic_id)
        .subquery()
    )
    articles = (
        select(
            ArticleTopic.topic_id,
            func.count()
            .filter(Article.published_to_feed_at >= start, Article.published_to_feed_at <= now)
            .label("publications"),
            func.max(Article.published_to_feed_at).label("last_published_at"),
        )
        .join(Article, Article.id == ArticleTopic.article_id)
        .where(
            Article.publication_status == "published",
            ArticleTopic.role.in_(["primary", "supporting"]),
        )
        .group_by(ArticleTopic.topic_id)
        .subquery()
    )
    query = (
        select(
            Topic.id,
            Topic.name,
            Topic.logo_url,
            func.coalesce(follows.c.followers, 0).label("followers"),
            func.coalesce(inferred.c.users, 0).label("inferred_users"),
            func.coalesce(articles.c.publications, 0).label("publications"),
            articles.c.last_published_at,
        )
        .outerjoin(follows, follows.c.topic_id == Topic.id)
        .outerjoin(inferred, inferred.c.topic_id == Topic.id)
        .outerjoin(articles, articles.c.topic_id == Topic.id)
        .where(Topic.status == "active", (follows.c.followers > 0) | (inferred.c.users > 0))
        .order_by(
            func.coalesce(follows.c.followers, 0).desc(),
            func.coalesce(inferred.c.users, 0).desc(),
            Topic.name,
            Topic.id,
        )
        .limit(10)
    )
    return [OverviewInterestCoverage(**row) for row in session.execute(query).mappings()]


def sources_performance(session, start, now):
    # Aggregate each relationship before joining: origins and followers must not multiply.
    articles = (
        select(
            ArticleOrigin.source_id,
            func.count(func.distinct(Article.id))
            .filter(Article.discovered_at >= start, Article.discovered_at <= now)
            .label("discovered"),
            func.count(func.distinct(Article.id))
            .filter(
                Article.published_to_feed_at >= start,
                Article.published_to_feed_at <= now,
                Article.publication_status == "published",
            )
            .label("published"),
        )
        .join(Article, Article.id == ArticleOrigin.article_id)
        .where(
            ((Article.discovered_at >= start) & (Article.discovered_at <= now))
            | ((Article.published_to_feed_at >= start) & (Article.published_to_feed_at <= now))
        )
        .group_by(ArticleOrigin.source_id)
        .subquery()
    )
    follows = (
        select(UserSource.source_id, func.count().label("followers"))
        .group_by(UserSource.source_id)
        .subquery()
    )
    failures = (
        select(IngestionJob.source_id, func.count().label("failures"))
        .where(
            IngestionJob.status == "failed",
            IngestionJob.finished_at >= start,
            IngestionJob.finished_at <= now,
        )
        .group_by(IngestionJob.source_id)
        .subquery()
    )
    base = (
        select(
            Source.id,
            Source.name,
            Source.logo_url,
            Source.consecutive_failures,
            Source.last_success_at,
            func.coalesce(articles.c.discovered, 0).label("discovered"),
            func.coalesce(articles.c.published, 0).label("published"),
            func.coalesce(follows.c.followers, 0).label("followers"),
            func.coalesce(failures.c.failures, 0).label("fetch_failures"),
        )
        .outerjoin(articles, articles.c.source_id == Source.id)
        .outerjoin(follows, follows.c.source_id == Source.id)
        .outerjoin(failures, failures.c.source_id == Source.id)
        .where(Source.approval_status == "approved", Source.enabled)
    )
    output = [
        OverviewSourcePerformance(**row)
        for row in session.execute(
            base.order_by(
                func.coalesce(articles.c.published, 0).desc(),
                func.coalesce(articles.c.discovered, 0).desc(),
                Source.name,
                Source.id,
            ).limit(12)
        ).mappings()
    ]
    failing = [
        OverviewSourcePerformance(**row)
        for row in session.execute(
            base.where(Source.consecutive_failures > 0)
            .order_by(
                Source.consecutive_failures.desc(),
                Source.last_success_at.asc().nullsfirst(),
                Source.id,
            )
            .limit(5)
        ).mappings()
    ]
    return output, failing


def processing(session, start, now):
    statements = []
    for kind, definition in JOB_DEFINITIONS.items():
        model = definition.model
        duration = getattr(model, "duration_ms", literal(0))
        measured = (duration > 0) & (model.finished_at >= start) & (model.finished_at <= now)
        statements.append(
            select(
                literal(kind).label("kind"),
                func.count().filter(model.status == "queued").label("queued"),
                func.count().filter(model.status == "running").label("running"),
                func.count()
                .filter(
                    model.status == "succeeded",
                    model.finished_at >= start,
                    model.finished_at <= now,
                )
                .label("completed"),
                func.count()
                .filter(
                    model.status == "failed", model.finished_at >= start, model.finished_at <= now
                )
                .label("failed"),
                func.min(model.created_at)
                .filter(model.status == "queued")
                .label("oldest_queued_at"),
                func.coalesce(func.sum(duration).filter(measured), 0).label("duration_ms"),
                func.count().filter(measured).label("duration_runs"),
            ).select_from(model)
        )
    return [OverviewProcessing(**row) for row in session.execute(union_all(*statements)).mappings()]


def overview_insights(session, days, now):
    start = datetime.combine(now.date() - timedelta(days=days - 1), time.min, tzinfo=now.tzinfo)
    previous_start = start - timedelta(days=days)
    daily = read_daily_metrics(session, previous_start, now)

    def metric(key):
        current = [r[key] for day, r in daily.items() if day >= start.date()]
        previous = [r[key] for day, r in daily.items() if day < start.date()]
        return OverviewMetric(
            current=sum(current) if all(v is not None for v in current) else None,
            previous=sum(previous) if all(v is not None for v in previous) else None,
        )

    elapsed = func.extract("epoch", Article.published_to_feed_at - Article.discovered_at)
    latency = session.execute(
        select(
            func.percentile_cont(0.5)
            .within_group(elapsed)
            .filter(Article.published_to_feed_at >= start),
            func.percentile_cont(0.5)
            .within_group(elapsed)
            .filter(Article.published_to_feed_at < start),
            func.percentile_cont(0.9)
            .within_group(elapsed)
            .filter(Article.published_to_feed_at >= start),
        ).where(
            Article.published_to_feed_at >= previous_start,
            Article.published_to_feed_at <= now,
            elapsed >= 0,
        )
    ).one()
    retained_start = max(
        start,
        datetime.combine(
            (now - timedelta(days=30)).date() + timedelta(days=1), time.min, tzinfo=now.tzinfo
        ),
    )
    opens = (
        select(ArticleOpen.article_id, func.count().label("opens"))
        .where(ArticleOpen.opened_hour >= retained_start, ArticleOpen.opened_hour <= now)
        .group_by(ArticleOpen.article_id)
        .subquery()
    )
    likes = (
        select(ArticleLike.article_id, func.count().label("likes"))
        .group_by(ArticleLike.article_id)
        .subquery()
    )
    popular = session.execute(
        select(
            Article.id, Article.title, opens.c.opens, func.coalesce(likes.c.likes, 0).label("likes")
        )
        .join(opens, opens.c.article_id == Article.id)
        .outerjoin(likes, likes.c.article_id == Article.id)
        .order_by(opens.c.opens.desc(), Article.id)
        .limit(10)
    ).mappings()
    sources, failing = sources_performance(session, start, now)
    oldest = session.execute(
        union_all(
            *[
                select(literal(kind), func.min(timestamp)).select_from(model).where(condition)
                for kind, model, timestamp, condition in [
                    (
                        "articles",
                        Article,
                        Article.discovered_at,
                        Article.review_status == "pending",
                    ),
                    ("sources", Source, Source.created_at, Source.approval_status == "pending"),
                    (
                        "topics",
                        TopicProposal,
                        TopicProposal.created_at,
                        TopicProposal.status == "pending",
                    ),
                    (
                        "relationships",
                        TopicRelationProposal,
                        TopicRelationProposal.created_at,
                        TopicRelationProposal.status == "pending",
                    ),
                ]
            ]
        )
    ).all()
    jobs = processing(session, start, now)
    measured_jobs = [job for job in jobs if job.kind in ("analysis", "topic-analysis")]
    duration_runs = sum(job.duration_runs for job in measured_jobs)
    return OverviewInsights(
        publications=metric("published"),
        opens=metric("opens"),
        accounts=metric("accounts"),
        publication_seconds=OverviewMetric(current=latency[0], previous=latency[1]),
        publication_p90_seconds=latency[2],
        reader_activity=[
            OverviewReaderDay(date=day.isoformat(), **values)
            for day, values in daily.items()
            if day >= start.date()
        ],
        top_articles=[OverviewPopularArticle(**r) for r in popular],
        top_articles_days=min(days, 30),
        coverage=interest_coverage(session, start, now),
        source_performance=sources,
        failing_sources=failing,
        personalization=personalization(session, now),
        processing=jobs,
        analysis_average_seconds=sum(job.duration_ms for job in measured_jobs)
        / duration_runs
        / 1000
        if duration_runs
        else None,
        oldest_review_at=dict(oldest),
    )
