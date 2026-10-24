"""Project topic call facts once, preserving lifetime stage and token accounting."""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    # ORM flushes can update runs before proposals, even when row locks were taken
    # in the opposite order. Release both admission locks whenever either is busy;
    # never hold one source lock while waiting for a writer to release the other.
    # Exclude run row lockers too: backfill's foreign-key checks acquire row locks.
    # Reads continue; source writes pause once both locks are held through commit.
    op.execute("""DO $body$
        DECLARE deadline timestamptz := clock_timestamp() + interval '30 seconds';
        BEGIN
          LOOP
            BEGIN
              LOCK TABLE topic_proposals IN SHARE MODE NOWAIT;
              LOCK TABLE topic_decision_runs IN EXCLUSIVE MODE NOWAIT;
              EXIT;
            EXCEPTION WHEN lock_not_available THEN
              IF clock_timestamp() >= deadline THEN RAISE; END IF;
            END;
            PERFORM pg_sleep(0.05);
          END LOOP;
        END $body$""")
    op.execute("""CREATE TABLE topic_decision_calls (
        proposal_id uuid NOT NULL REFERENCES topic_decision_runs(proposal_id) ON DELETE CASCADE,
        ordinal bigint NOT NULL, stage text, stage_call bigint NOT NULL,
        started_at timestamptz, tokens numeric NOT NULL,
        charged_tokens bigint, escalated boolean NOT NULL,
        PRIMARY KEY (proposal_id, ordinal))""")
    # One projection is shared by backfill and live writes. Scalar array entries
    # still occupy their original ordinal and retain an unknown token contribution.
    # Numeric addition avoids overflowing when individually valid bigint fields add up.
    op.execute("""CREATE FUNCTION topic_decision_call_values(call_array jsonb)
        RETURNS TABLE (ordinal bigint, stage text, stage_call bigint, started_at timestamptz,
            tokens numeric, charged_tokens bigint, escalated boolean)
        LANGUAGE sql STABLE SET TimeZone = 'UTC' AS $body$
        WITH calls AS (
          SELECT c, n AS ordinal FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(call_array) = 'array' THEN call_array ELSE '[]'::jsonb END
          ) WITH ORDINALITY AS items(c, n)
        ), parsed AS (
          SELECT *,
            CASE WHEN pg_input_is_valid(c#>>'{tokens,totalTokens}', 'bigint')
              THEN (c#>>'{tokens,totalTokens}')::numeric ELSE 0 END AS total_tokens,
            CASE WHEN pg_input_is_valid(c#>>'{tokens,inputTokens}', 'bigint')
              THEN (c#>>'{tokens,inputTokens}')::numeric ELSE 0 END AS input_tokens,
            CASE WHEN pg_input_is_valid(c#>>'{tokens,outputTokens}', 'bigint')
              THEN (c#>>'{tokens,outputTokens}')::numeric ELSE 0 END AS output_tokens
          FROM calls
        )
        SELECT ordinal, c->>'stage',
          row_number() OVER (PARTITION BY c->>'stage' ORDER BY ordinal),
          -- Relative and infinite timestamps would move historical facts on a later
          -- correction or rebuild. Only finite ISO dates/date-times are telemetry.
          CASE WHEN c->>'started_at' ~ (
            '^[0-9]{4}-[0-9]{2}-[0-9]{2}([Tt ][0-9]{2}:[0-9]{2}' ||
            '(:[0-9]{2}([.][0-9]+)?)?([Zz]|[+-][0-9]{2}(:?[0-9]{2})?)?)?$')
            AND pg_input_is_valid(c->>'started_at', 'timestamptz')
            THEN (c->>'started_at')::timestamptz END,
          greatest(total_tokens, input_tokens + output_tokens),
          CASE WHEN pg_input_is_valid(c->>'charged_tokens', 'bigint')
            THEN (c->>'charged_tokens')::bigint END,
          CASE WHEN pg_input_is_valid(c->>'escalated', 'boolean')
            THEN (c->>'escalated')::boolean ELSE false END
        FROM parsed
        $body$""")
    op.execute("""INSERT INTO topic_decision_calls
        SELECT r.proposal_id, calls.* FROM topic_decision_runs r
        CROSS JOIN LATERAL topic_decision_call_values(r.state->'calls') calls""")
    op.execute("""CREATE FUNCTION topic_decision_calls_sync() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        DECLARE call_count integer;
        BEGIN
          IF TG_OP = 'UPDATE' AND OLD.state->'calls' IS NOT DISTINCT FROM NEW.state->'calls' THEN
            RETURN NULL;
          END IF;
          INSERT INTO topic_decision_calls AS target
          SELECT NEW.proposal_id, calls.* FROM topic_decision_call_values(NEW.state->'calls') calls
          ON CONFLICT (proposal_id, ordinal) DO UPDATE SET
            (stage, stage_call, started_at, tokens, charged_tokens, escalated) =
            (excluded.stage, excluded.stage_call, excluded.started_at,
             excluded.tokens, excluded.charged_tokens, excluded.escalated)
          WHERE (target.stage, target.stage_call, target.started_at,
                 target.tokens, target.charged_tokens, target.escalated)
            IS DISTINCT FROM (excluded.stage, excluded.stage_call, excluded.started_at,
                              excluded.tokens, excluded.charged_tokens, excluded.escalated);
          call_count := CASE WHEN jsonb_typeof(NEW.state->'calls') = 'array'
            THEN jsonb_array_length(NEW.state->'calls') ELSE 0 END;
          DELETE FROM topic_decision_calls
            WHERE proposal_id = NEW.proposal_id AND ordinal > call_count;
          RETURN NULL;
        END $body$""")
    op.execute("""CREATE TRIGGER topic_decision_calls_sync
        AFTER INSERT OR UPDATE OF state ON topic_decision_runs
        FOR EACH ROW EXECUTE FUNCTION topic_decision_calls_sync()""")
    # The FK propagates deletion and TRUNCATE ... CASCADE without a second writer.
    op.create_index("ix_topic_decision_calls_started_at", "topic_decision_calls", ["started_at"])
    op.create_index("ix_topic_proposals_reviewed_at", "topic_proposals", ["reviewed_at"])


def downgrade():
    op.drop_index("ix_topic_proposals_reviewed_at", table_name="topic_proposals")
    op.execute("DROP TRIGGER topic_decision_calls_sync ON topic_decision_runs")
    op.execute("DROP FUNCTION topic_decision_calls_sync()")
    op.execute("DROP FUNCTION topic_decision_call_values(jsonb)")
    op.drop_table("topic_decision_calls")
