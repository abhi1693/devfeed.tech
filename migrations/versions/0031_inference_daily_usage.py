"""Maintain exact daily invocation totals in the ledger writer's transaction."""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

METRICS = (
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
KEYS = "day, operation, model, model_is_null, effort"
NUMERIC = ", ".join(
    f"CASE WHEN tokens->>'{key}' ~ '^[0-9]{{1,12}}$' "
    f"THEN (tokens->>'{key}')::bigint ELSE 0 END AS {name}"
    for name, key in (
        ("input_tokens", "inputTokens"),
        ("cached_input_tokens", "cachedInputTokens"),
        ("output_tokens", "outputTokens"),
        ("reasoning_tokens", "reasoningOutputTokens"),
    )
)
PROJECTION = """
(started_at AT TIME ZONE 'UTC')::date AS day, operation,
coalesce(model, 'Unknown model') AS model, model IS NULL AS model_is_null,
coalesce(reasoning_effort, 'Inherited / unknown') AS effort
"""
TOTALS = """
sum(sign) AS calls,
sum(sign * (status = 'returned')::int) AS returned,
sum(sign * (status = 'failed')::int) AS failed,
sum(sign * (status = 'running')::int) AS running,
sum(sign * (input_tokens + output_tokens = 0)::int) AS unreported,
sum(sign * input_tokens) AS input_tokens,
sum(sign * least(input_tokens, cached_input_tokens)) AS cached_input_tokens,
sum(sign * output_tokens) AS output_tokens,
sum(sign * least(output_tokens, reasoning_tokens)) AS reasoning_tokens,
sum(sign::bigint * web_searches) AS web_searches, sum(sign::bigint * duration_ms) AS duration_ms
"""


def upgrade():
    # Block ledger writes through backfill + trigger installation, without blocking readers.
    op.execute("LOCK TABLE inference_calls IN SHARE ROW EXCLUSIVE MODE")
    columns = ", ".join(f"{name} numeric NOT NULL DEFAULT 0" for name in METRICS)
    op.execute(f"""CREATE TABLE inference_daily_usage (
        day date NOT NULL, operation varchar(50) NOT NULL, model varchar(100) NOT NULL,
        model_is_null boolean NOT NULL, effort varchar(30) NOT NULL, {columns},
        PRIMARY KEY ({KEYS}))""")
    op.execute(f"""INSERT INTO inference_daily_usage
        WITH values AS (SELECT *, 1::int AS sign, {NUMERIC} FROM inference_calls)
        SELECT {PROJECTION}, {TOTALS} FROM values GROUP BY 1,2,3,4,5""")
    deltas = ", ".join(f"inference_daily_usage.{name} + excluded.{name}" for name in METRICS)
    upsert = f"""WITH values AS (SELECT *, {NUMERIC} FROM (%s) changed),
        totals AS (SELECT {PROJECTION}, {TOTALS} FROM values GROUP BY 1,2,3,4,5)
        INSERT INTO inference_daily_usage SELECT * FROM totals ORDER BY {KEYS}
        ON CONFLICT ({KEYS}) DO UPDATE SET ({", ".join(METRICS)}) = ({deltas})"""
    cleanup = f"""DELETE FROM inference_daily_usage target USING
        (SELECT DISTINCT {PROJECTION} FROM (%s) changed) keys
        WHERE target.calls = 0 AND (target.day,target.operation,target.model,
        target.model_is_null,target.effort) =
        (keys.day,keys.operation,keys.model,keys.model_is_null,keys.effort)"""
    # Source SQL is selected only from these fixed transition-table queries, never user data.
    op.execute(f"""CREATE FUNCTION inference_daily_usage_delta() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        DECLARE source_sql text;
        BEGIN
          IF TG_OP = 'INSERT' THEN source_sql := 'SELECT 1 AS sign, * FROM new_rows';
          ELSIF TG_OP = 'DELETE' THEN source_sql := 'SELECT -1 AS sign, * FROM old_rows';
          ELSE source_sql := 'SELECT 1 AS sign, * FROM new_rows ' ||
            'UNION ALL SELECT -1 AS sign, * FROM old_rows';
          END IF;
          EXECUTE format($sql${upsert}$sql$, source_sql);
          EXECUTE format($sql${cleanup}$sql$, source_sql);
          RETURN NULL;
        END $body$""")
    for event, tables in (
        ("INSERT", "NEW TABLE AS new_rows"),
        ("DELETE", "OLD TABLE AS old_rows"),
        ("UPDATE", "OLD TABLE AS old_rows NEW TABLE AS new_rows"),
    ):
        op.execute(f"""CREATE TRIGGER inference_daily_usage_{event.lower()}
            AFTER {event} ON inference_calls REFERENCING {tables}
            FOR EACH STATEMENT EXECUTE FUNCTION inference_daily_usage_delta()""")
    op.execute("""CREATE FUNCTION inference_daily_usage_clear() RETURNS trigger
        LANGUAGE plpgsql AS $body$ BEGIN
          DELETE FROM inference_daily_usage; RETURN NULL;
        END $body$""")
    op.execute("""CREATE TRIGGER inference_daily_usage_truncate AFTER TRUNCATE ON inference_calls
        FOR EACH STATEMENT EXECUTE FUNCTION inference_daily_usage_clear()""")


def downgrade():
    for event in ("truncate", "update", "delete", "insert"):
        op.execute(f"DROP TRIGGER inference_daily_usage_{event} ON inference_calls")
    op.execute("DROP FUNCTION inference_daily_usage_clear()")
    op.execute("DROP FUNCTION inference_daily_usage_delta()")
    op.drop_table("inference_daily_usage")
