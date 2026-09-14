"""Read-only chart series from per-invocation telemetry introduced in 0008."""

from datetime import datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import text


class InferencePoint(BaseModel):
    date: str
    operation: str
    model: str
    effort: str
    calls: int
    returned: int
    failed: int
    running: int
    unreported: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    web_searches: int
    duration_ms: int


class TopicDecisionDay(BaseModel):
    date: str
    approved: int = 0
    rejected: int = 0
    deferred: int = 0
    calls: int = 0
    escalations: int = 0
    repeated_stages: int = 0


class InferenceCharts(BaseModel):
    first_recorded_at: datetime | None = None
    activity: list[InferencePoint] = Field(default_factory=list)
    topic_activity: list[TopicDecisionDay] = Field(default_factory=list)


def inference_charts(session, start: datetime, now: datetime) -> InferenceCharts:
    # Only accept numeric telemetry, exactly as the job-level chart does. Cached
    # input and reasoning are subsets, never additional tokens in totals.
    numeric = {
        name: f"CASE WHEN tokens->>'{key}' ~ '^[0-9]{{1,12}}$' "
        f"THEN (tokens->>'{key}')::bigint ELSE 0 END"
        for name, key in {
            "input_tokens": "inputTokens",
            "cached_input_tokens": "cachedInputTokens",
            "output_tokens": "outputTokens",
            "reasoning_tokens": "reasoningOutputTokens",
        }.items()
    }
    expressions = ", ".join(f"({value}) AS {name}" for name, value in numeric.items())
    rows = (
        session.execute(
            text(f"""
        WITH usage AS (
          SELECT *, {expressions} FROM inference_calls
          WHERE started_at BETWEEN :start AND :now
        )
        SELECT to_char(started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS date,
          operation, coalesce(model, 'Unknown model') AS model,
          coalesce(reasoning_effort, 'Inherited / unknown') AS effort,
          count(*) AS calls, count(*) FILTER (WHERE status = 'returned') AS returned,
          count(*) FILTER (WHERE status = 'failed') AS failed,
          count(*) FILTER (WHERE status = 'running') AS running,
          count(*) FILTER (WHERE input_tokens + output_tokens = 0) AS unreported,
          sum(input_tokens) AS input_tokens,
          sum(least(input_tokens, cached_input_tokens)) AS cached_input_tokens,
          sum(output_tokens) AS output_tokens,
          sum(least(output_tokens, reasoning_tokens)) AS reasoning_tokens,
          sum(web_searches) AS web_searches, sum(duration_ms) AS duration_ms
        FROM usage GROUP BY date, operation, model, effort ORDER BY date, operation, model, effort
    """),
            {"start": start, "now": now},
        )
        .mappings()
        .all()
    )
    days = {}
    day = start.date()
    while day <= now.date():
        key = day.isoformat()
        days[key] = TopicDecisionDay(date=key)
        day += timedelta(days=1)
    outcomes = session.execute(
        text("""
        SELECT to_char(reviewed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS date,
          count(*) FILTER (WHERE status = 'approved') AS approved,
          count(*) FILTER (WHERE status = 'rejected') AS rejected
        FROM topic_proposals WHERE reviewed_at BETWEEN :start AND :now GROUP BY date
    """),
        {"start": start, "now": now},
    ).mappings()
    for row in outcomes:
        days[row["date"]].approved, days[row["date"]].rejected = row["approved"], row["rejected"]
    calls = session.execute(
        text("""
        WITH calls AS (
          SELECT proposal_id, c, n,
            row_number() OVER (PARTITION BY proposal_id, c->>'stage' ORDER BY n) AS stage_call
          FROM topic_decision_runs,
            jsonb_array_elements(state->'calls') WITH ORDINALITY AS items(c, n)
        )
        SELECT to_char((c->>'started_at')::timestamptz AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS date,
          count(*) AS calls,
          count(*) FILTER (WHERE (c->>'escalated')::boolean) AS escalations,
          count(*) FILTER (WHERE stage_call > 1) AS repeated_stages
        FROM calls WHERE (c->>'started_at')::timestamptz BETWEEN :start AND :now GROUP BY date
    """),
        {"start": start, "now": now},
    ).mappings()
    for row in calls:
        for key in ("calls", "escalations", "repeated_stages"):
            setattr(days[row["date"]], key, row[key])
    deferred = session.execute(
        text("""
        SELECT to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS date, count(*) AS count
        FROM topic_decision_runs WHERE status = 'deferred'
          AND updated_at BETWEEN :start AND :now GROUP BY date
    """),
        {"start": start, "now": now},
    ).mappings()
    for row in deferred:
        days[row["date"]].deferred = row["count"]
    first = session.scalar(text("SELECT min(started_at) FROM inference_calls"))
    return InferenceCharts(
        first_recorded_at=first,
        activity=[InferencePoint(**dict(row)) for row in rows],
        topic_activity=list(days.values()),
    )
