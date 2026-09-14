"""Independent, read-only data sources for Overview charts."""

from datetime import datetime, time, timedelta
from typing import Literal

from devfeed_core.inference_metrics import inference_charts
from devfeed_core.models import (
    Article,
    ArticleLike,
    ArticleOpen,
    Source,
    TopicProposal,
    TopicRelationProposal,
)
from devfeed_core.overview_daily import read_daily_metrics
from devfeed_core.pipeline_metrics import pipeline_throughput
from devfeed_core.topic_decision_metrics import decision_metrics
from pydantic import BaseModel, Field
from sqlalchemy import func, literal, select, union_all

from devfeed_admin_api.automation import (
    AutomationBlocker,
    AutomationOverview,
    analysis_token_activity,
    automation_blockers,
    publication_automation,
)
from devfeed_admin_api.overview_insights import (
    OverviewInsights,
    OverviewMetric,
    OverviewPopularArticle,
    OverviewReaderDay,
    interest_coverage,
    personalization,
    processing,
    reader_adoption,
    sources_performance,
)

PanelName = Literal[
    "publications",
    "clicks",
    "accounts",
    "publication-time",
    "attention",
    "readers",
    "click-depth",
    "new-accounts",
    "adoption",
    "publishing",
    "reader-activity",
    "popular-articles",
    "topic-coverage",
    "feed-health",
    "recommendation-reasons",
    "sources",
    "publication-automation",
    "job-tokens",
    "job-reliability",
    "workload",
    "blockers",
    "throughput",
    "decision-efficiency",
    "inference-tokens",
    "tokens-by-task",
    "tokens-by-model",
    "reasoning-effort",
    "inference-outcomes",
    "web-searches",
    "topic-outcomes",
    "topic-backlog",
    "repeated-stages",
    "inference-duration",
]
GROUPS = {
    **dict.fromkeys(
        (
            "publications",
            "clicks",
            "accounts",
            "readers",
            "click-depth",
            "new-accounts",
            "publishing",
            "reader-activity",
        ),
        "daily",
    ),
    **dict.fromkeys(("feed-health", "recommendation-reasons"), "personalization"),
    **dict.fromkeys(("job-reliability", "workload"), "processing"),
    **dict.fromkeys(
        (
            "inference-tokens",
            "tokens-by-task",
            "tokens-by-model",
            "reasoning-effort",
            "inference-outcomes",
            "web-searches",
            "inference-duration",
            "topic-outcomes",
            "repeated-stages",
        ),
        "inference",
    ),
    **dict.fromkeys(("decision-efficiency", "topic-backlog"), "decisions"),
}


class OverviewPanelAutomation(AutomationOverview):
    blockers: list[AutomationBlocker] = Field(default_factory=list)
    published_in_window: int = 0
    published_without_intervention: int = 0
    automatic_publication_percent: float | None = None
    median_ingestion_to_publication_seconds: float | None = None
    analysis_tokens: int = 0
    analysis_duration_ms: int = 0
    usage_reported_runs: int = 0


class OverviewPanel(BaseModel):
    generated_at: datetime
    days: int
    insights: OverviewInsights = Field(default_factory=OverviewInsights)
    automation: OverviewPanelAutomation | None = None
    articles_pending_review: int = 0
    sources_pending_review: int = 0
    topic_proposals_pending: int = 0
    relationship_proposals_pending: int = 0
    sources_failing: int = 0


def panel_data(session, group: str, days: int, now: datetime) -> OverviewPanel:
    start = datetime.combine(now.date() - timedelta(days=days - 1), time.min, tzinfo=now.tzinfo)
    previous = start - timedelta(days=days)
    result = OverviewPanel(generated_at=now, days=days)
    insight = result.insights
    if group in {"daily", "publication-time"}:
        daily = read_daily_metrics(session, previous, now)
        insight.reader_activity = [
            OverviewReaderDay(date=day.isoformat(), **values)
            for day, values in daily.items()
            if day >= start.date()
        ]
        for field, key in (
            ("publications", "published"),
            ("opens", "opens"),
            ("accounts", "accounts"),
        ):
            current = [r[key] for day, r in daily.items() if day >= start.date()]
            old = [r[key] for day, r in daily.items() if day < start.date()]
            setattr(
                insight,
                field,
                OverviewMetric(
                    current=sum(current) if all(v is not None for v in current) else None,
                    previous=sum(old) if all(v is not None for v in old) else None,
                ),
            )
        if group == "publication-time":
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
                    Article.published_to_feed_at >= previous,
                    Article.published_to_feed_at <= now,
                    elapsed >= 0,
                )
            ).one()
            insight.publication_seconds = OverviewMetric(current=latency[0], previous=latency[1])
            insight.publication_p90_seconds = latency[2]
    elif group == "adoption":
        insight.adoption = reader_adoption(session)
    elif group == "personalization":
        insight.personalization = personalization(session, now)
    elif group == "topic-coverage":
        insight.coverage = interest_coverage(session, start, now)
    elif group == "sources":
        insight.source_performance, insight.failing_sources, insight.source_publications_total = (
            sources_performance(session, start, now)
        )
    elif group == "processing":
        insight.processing = processing(session, start, now)
    elif group == "popular-articles":
        retained = max(
            start,
            datetime.combine(
                (now - timedelta(days=30)).date() + timedelta(days=1), time.min, tzinfo=now.tzinfo
            ),
        )
        opens = (
            select(ArticleOpen.article_id, func.count().label("opens"))
            .where(ArticleOpen.opened_hour >= retained, ArticleOpen.opened_hour <= now)
            .group_by(ArticleOpen.article_id)
            .subquery()
        )
        likes = (
            select(ArticleLike.article_id, func.count().label("likes"))
            .group_by(ArticleLike.article_id)
            .subquery()
        )
        rows = session.execute(
            select(
                Article.id,
                Article.title,
                opens.c.opens,
                func.coalesce(likes.c.likes, 0).label("likes"),
            )
            .join(opens, opens.c.article_id == Article.id)
            .outerjoin(likes, likes.c.article_id == Article.id)
            .order_by(opens.c.opens.desc(), Article.id)
            .limit(10)
        ).mappings()
        insight.top_articles = [OverviewPopularArticle(**r) for r in rows]
        insight.top_articles_days = min(days, 30)
        insight.reader_activity = [
            OverviewReaderDay(date=day.isoformat(), **values)
            for day, values in read_daily_metrics(session, retained, now).items()
        ]
    elif group == "attention":
        rows = session.execute(
            union_all(
                *[
                    select(literal(key), func.count(), func.min(timestamp))
                    .select_from(model)
                    .where(condition)
                    for key, model, timestamp, condition in [
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
        attributes = {
            "articles": "articles_pending_review",
            "sources": "sources_pending_review",
            "topics": "topic_proposals_pending",
            "relationships": "relationship_proposals_pending",
        }
        for key, count, oldest in rows:
            setattr(result, attributes[key], count)
            insight.oldest_review_at[key] = oldest
        result.sources_failing = (
            session.scalar(
                select(func.count())
                .select_from(Source)
                .where(
                    Source.enabled,
                    Source.approval_status == "approved",
                    Source.consecutive_failures > 0,
                )
            )
            or 0
        )
    else:
        automation = OverviewPanelAutomation()
        result.automation = automation
        if group == "blockers":
            automation.blockers = automation_blockers(session)
        elif group == "publication-automation":
            for key, value in publication_automation(session, start, now).items():
                setattr(automation, key, value)
        elif group == "job-tokens":
            automation.token_activity, automation.analysis_duration_ms = analysis_token_activity(
                session, start, now
            )
            automation.analysis_tokens = sum(
                day.article_analysis + day.topic_analysis + day.research_verification
                for day in automation.token_activity
            )
            automation.usage_reported_runs = sum(
                day.reported_runs for day in automation.token_activity
            )
        elif group == "throughput":
            automation.throughput = pipeline_throughput(session, now)
        elif group == "decisions":
            automation.topic_decisions = decision_metrics(session, start, now)
        elif group == "inference":
            automation.inference = inference_charts(session, start, now)
        else:
            raise ValueError("Unknown Overview panel")
    return result
