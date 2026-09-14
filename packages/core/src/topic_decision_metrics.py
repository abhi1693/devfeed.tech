"""Count review outcomes and whole-workflow usage, including unfinished work."""

from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import text


class TopicDecisionMetrics(BaseModel):
    pending: int = 0
    actionable: int = 0
    deferred: int = 0
    awaiting_review: int = 0
    approved: int = 0
    rejected: int = 0
    decisions_per_hour: float = 0
    estimated_drain_hours: float | None = None
    calls: int = 0
    tokens: int = 0
    charged_tokens: int = 0
    unreported_calls: int = 0
    repeat_calls: int = 0
    escalations: int = 0
    escalation_percent: float | None = None
    tokens_per_completed_topic: float | None = None
    deferred_reasons: dict[str, int] = Field(default_factory=dict)


def decision_metrics(session, start: datetime, now: datetime) -> TopicDecisionMetrics:
    row = (
        session.execute(
            text("""
        SELECT count(*) FILTER (WHERE p.status = 'pending') AS pending,
          count(*) FILTER (WHERE p.status = 'pending'
            AND (r.status IS NULL OR r.status = 'active')) AS actionable,
          count(*) FILTER (WHERE p.status = 'pending' AND r.status = 'deferred') AS deferred,
          count(*) FILTER (WHERE p.status = 'pending' AND r.status = 'review') AS awaiting_review,
          count(*) FILTER (WHERE p.status = 'approved'
            AND p.reviewed_at BETWEEN :start AND :now) AS approved,
          count(*) FILTER (WHERE p.status = 'rejected'
            AND p.reviewed_at BETWEEN :start AND :now) AS rejected
        FROM topic_proposals p LEFT JOIN topic_decision_runs r ON r.proposal_id = p.id
    """),
            {"start": start, "now": now},
        )
        .mappings()
        .one()
    )
    result = TopicDecisionMetrics(**dict(row))
    hours = max((now - start).total_seconds() / 3600, 1 / 3600)
    result.decisions_per_hour = round((result.approved + result.rejected) / hours, 2)
    if result.decisions_per_hour:
        result.estimated_drain_hours = round(result.actionable / result.decisions_per_hour, 1)
    usage = (
        session.execute(
            text("""
        WITH calls AS (
          SELECT r.proposal_id, r.status, c, n,
            (p.status IN ('approved', 'rejected')
            AND p.reviewed_at BETWEEN :start AND :now) AS completed
          FROM topic_decision_runs r JOIN topic_proposals p ON p.id = r.proposal_id,
               jsonb_array_elements(r.state->'calls') WITH ORDINALITY AS items(c, n)
        ), totals AS (
          SELECT *, greatest(coalesce((c->'tokens'->>'totalTokens')::bigint, 0),
            coalesce((c->'tokens'->>'inputTokens')::bigint, 0)
              + coalesce((c->'tokens'->>'outputTokens')::bigint, 0)) AS tokens,
            (c->>'started_at')::timestamptz BETWEEN :start AND :now AS in_window,
            row_number() OVER (PARTITION BY proposal_id, c->>'stage' ORDER BY n) AS stage_call
          FROM calls
        )
        SELECT count(*) FILTER (WHERE in_window) AS calls,
          coalesce(sum(tokens) FILTER (WHERE in_window), 0) AS tokens,
          coalesce(sum((c->>'charged_tokens')::bigint)
            FILTER (WHERE in_window), 0) AS charged_tokens,
          count(*) FILTER (WHERE in_window AND tokens = 0) AS unreported_calls,
          count(*) FILTER (WHERE in_window AND (c->>'escalated')::boolean) AS escalations,
          count(*) FILTER (WHERE in_window AND stage_call > 1) AS repeat_calls,
          CASE WHEN bool_and(tokens > 0) FILTER (WHERE completed)
            THEN sum(tokens) FILTER (WHERE completed)::float
              / nullif(count(DISTINCT proposal_id) FILTER (WHERE completed), 0)
            ELSE NULL END AS tokens_per_completed_topic
        FROM totals
    """),
            {"start": start, "now": now},
        )
        .mappings()
        .one()
    )
    # PostgreSQL SUM(bigint) returns Decimal. Validate instead of bypassing field
    # conversion with setattr, or cold and cached JSON responses differ in type.
    result = TopicDecisionMetrics.model_validate({**result.model_dump(), **dict(usage)})
    result.escalation_percent = (
        round(100 * result.escalations / result.calls, 1) if result.calls else None
    )
    result.deferred_reasons = dict(
        session.execute(
            text("""
        SELECT coalesce(r.reason, 'unspecified'), count(*) FROM topic_decision_runs r
        JOIN topic_proposals p ON p.id = r.proposal_id
        WHERE r.status = 'deferred' AND p.status = 'pending' GROUP BY r.reason
    """)
        ).all()
    )
    return result
