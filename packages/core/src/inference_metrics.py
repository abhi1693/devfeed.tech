"""Read-only chart series from per-invocation telemetry introduced in 0008."""

from datetime import UTC, datetime, timedelta

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


def _token_expressions() -> str:
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
    return ", ".join(f"({value}) AS {name}" for name, value in numeric.items())


def _ledger_activity(session, start: datetime, now: datetime):
    # Only accept numeric telemetry, exactly as the job-level chart does. Cached
    # input and reasoning are subsets, never additional tokens in totals.
    expressions = _token_expressions()
    return (
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


def _daily_activity(session, start: datetime, now: datetime):
    expressions = _token_expressions()
    first_day = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
    last_day = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    first_day = first_day.replace(hour=0, minute=0, second=0, microsecond=0)
    last_day = last_day.replace(hour=0, minute=0, second=0, microsecond=0)
    metrics = (
        "calls",
        "returned",
        "failed",
        "running",
        "unreported",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "web_searches",
        "duration_ms",
    )
    sums = ", ".join(f"sum({name}) AS {name}" for name in metrics)
    return (
        session.execute(
            text(f"""
        WITH range_calls AS (
          SELECT * FROM inference_calls
          WHERE started_at >= :start AND started_at <= :now AND started_at < :first_full_day
          UNION ALL
          SELECT * FROM inference_calls WHERE :last_day >= :first_full_day
            AND started_at >= :last_day AND started_at <= :now
        ), usage AS (
          SELECT *, {expressions} FROM range_calls
        ), boundary AS (
          SELECT (started_at AT TIME ZONE 'UTC')::date AS day, operation,
            coalesce(model, 'Unknown model') AS model, model IS NULL AS model_is_null,
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
          FROM usage GROUP BY 1,2,3,4,5
        ), combined AS (
          SELECT * FROM boundary UNION ALL
          SELECT * FROM inference_daily_usage
          WHERE day > :start_day AND day < :end_day AND calls > 0
        )
        SELECT to_char(day, 'YYYY-MM-DD') AS date, operation, model, effort, {sums}
        FROM combined GROUP BY day,operation,model,model_is_null,effort
        ORDER BY date,operation,model,effort
    """),
            {
                "start": start,
                "now": now,
                "first_full_day": first_day + timedelta(days=1),
                "last_day": last_day,
                "start_day": first_day.date(),
                "end_day": last_day.date(),
            },
        )
        .mappings()
        .all()
    )


def inference_charts(session, start: datetime, now: datetime) -> InferenceCharts:
    rows = (
        _daily_activity(session, start, now)
        if session.scalar(text("SELECT to_regclass('public.inference_daily_usage') IS NOT NULL"))
        else _ledger_activity(session, start, now)
    )
    days = {}
    day = (start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)).date()
    last_day = (now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)).date()
    while day <= last_day:
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
        SELECT to_char(started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS date,
          count(*) AS calls,
          count(*) FILTER (WHERE escalated) AS escalations,
          count(*) FILTER (WHERE stage_call > 1) AS repeated_stages
        FROM topic_decision_calls WHERE started_at BETWEEN :start AND :now GROUP BY date
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
