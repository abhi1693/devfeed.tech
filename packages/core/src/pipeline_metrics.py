"""Useful completions and queue pressure over a fixed rolling 24-hour window."""

from datetime import datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import text

from devfeed_core.pipeline_capacity import observe_capacity, topic_admission_limit


class PipelineHour(BaseModel):
    hour: datetime
    topic_decisions: int = 0
    articles_published: int = 0
    article_analyses: int = 0


class PipelineQueue(BaseModel):
    kind: str
    queued: int = 0
    running: int = 0
    oldest_due_seconds: float | None = None
    completed: int = 0
    failed: int = 0
    median_processing_seconds: float | None = None


class PipelineThroughput(BaseModel):
    hours: list[PipelineHour] = Field(default_factory=list)
    queues: list[PipelineQueue] = Field(default_factory=list)
    capacity_observed: bool = False
    topic_workers: int | None = None
    article_workers: int | None = None
    idle_topic_workers: int | None = None
    idle_article_workers: int | None = None
    shared_workers: int | None = None
    cooldown_seconds: int | None = None
    topic_admission_limit: int = 0
    topic_decisions_per_hour: float = 0
    articles_published_per_hour: float = 0


def pipeline_throughput(session, now: datetime) -> PipelineThroughput:
    # One bounded result with indexed timestamp ranges; no article bodies or prompts.
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    rows = (
        session.execute(
            text("""
        WITH events AS (
          SELECT p.reviewed_at AS at, 'topic_decisions' AS kind
          FROM topic_proposals p JOIN topic_decision_runs r ON r.proposal_id = p.id
          WHERE r.status = 'decided' AND p.status IN ('approved','rejected')
            AND p.reviewed_at BETWEEN :start AND :now
          UNION ALL
          SELECT published_to_feed_at, 'articles_published' FROM articles
            WHERE published_to_feed_at BETWEEN :start AND :now
          UNION ALL
          SELECT finished_at, 'article_analyses' FROM article_analysis_jobs
            WHERE status = 'succeeded' AND outcome = 'applied'
              AND finished_at BETWEEN :start AND :now
        )
        SELECT date_trunc('hour', at, 'UTC') AS hour, kind, count(*) AS count
        FROM events GROUP BY 1, 2
    """),
            {"start": start, "now": now},
        )
        .mappings()
        .all()
    )
    hours = {
        start + timedelta(hours=i): PipelineHour(hour=start + timedelta(hours=i)) for i in range(24)
    }
    for row in rows:
        setattr(hours[row["hour"]], row["kind"], row["count"])
    queue_rows = (
        session.execute(
            text("""
        WITH jobs AS (
          SELECT 'topic' AS kind, status, available_at, finished_at, duration_ms
          FROM topic_analysis_jobs WHERE proposal_id IS NOT NULL
            AND (status IN ('queued','running') OR finished_at BETWEEN :start AND :now)
          UNION ALL
          SELECT 'article', status, available_at, finished_at, duration_ms
          FROM article_analysis_jobs
          WHERE status IN ('queued','running') OR finished_at BETWEEN :start AND :now
        )
        SELECT kind, count(*) FILTER (WHERE status = 'queued') AS queued,
          count(*) FILTER (WHERE status = 'running') AS running,
          extract(epoch FROM :now - min(available_at) FILTER
            (WHERE status = 'queued' AND available_at <= :now)) AS oldest_due_seconds,
          count(*) FILTER (WHERE status = 'succeeded'
            AND finished_at BETWEEN :start AND :now) AS completed,
          count(*) FILTER (WHERE status = 'failed'
            AND finished_at BETWEEN :start AND :now) AS failed,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms / 1000.0)
            FILTER (WHERE finished_at BETWEEN :start AND :now AND duration_ms IS NOT NULL)
            AS median_processing_seconds
        FROM jobs GROUP BY kind
    """),
            {"start": start, "now": now},
        )
        .mappings()
        .all()
    )
    capacity = observe_capacity()
    elapsed = max((now - start).total_seconds() / 3600, 1 / 3600)
    return PipelineThroughput(
        hours=list(hours.values()),
        queues=[PipelineQueue(**dict(row)) for row in queue_rows],
        capacity_observed=capacity["observed"],
        **{
            k: capacity.get(k)
            for k in (
                "topic_workers",
                "article_workers",
                "idle_topic_workers",
                "idle_article_workers",
                "shared_workers",
                "cooldown_seconds",
            )
        },
        topic_admission_limit=topic_admission_limit(capacity),
        topic_decisions_per_hour=round(sum(h.topic_decisions for h in hours.values()) / elapsed, 2),
        articles_published_per_hour=round(
            sum(h.articles_published for h in hours.values()) / elapsed, 2
        ),
    )
