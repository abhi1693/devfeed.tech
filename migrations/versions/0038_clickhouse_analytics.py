"""Capture narrow analytics transactionally for asynchronous ClickHouse export."""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

# Frozen source projection: migrations must not depend on future application models.
SOURCES = {
    "articles": {
        "keys": ("id",),
        "fields": (
            "id",
            "discovered_at",
            "published_to_feed_at",
            "publication_status",
            "review_status",
            "content_type",
        ),
    },
    "sources": {
        "keys": ("id",),
        "fields": (
            "id",
            "name",
            "logo_url",
            "approval_status",
            "enabled",
            "consecutive_failures",
            "last_success_at",
            "created_at",
        ),
    },
    "topics": {"keys": ("id",), "fields": ("id", "name", "logo_url", "managed_logo", "status")},
    "article_topics": {
        "keys": ("article_id", "topic_id"),
        "fields": ("article_id", "topic_id", "role"),
    },
    "article_origins": {"keys": ("id",), "fields": ("id", "article_id", "source_id")},
    "user_accounts": {"keys": ("id",), "fields": ("id", "name", "profile", "created_at")},
    "article_likes": {"keys": ("article_id", "user_id"), "fields": ("article_id", "user_id")},
    "article_opens": {
        "keys": ("article_id", "viewer_key", "opened_hour"),
        "fields": ("article_id", "viewer_key", "opened_hour"),
    },
    "overview_daily": {"keys": ("day",), "fields": ("day", "metrics", "updated_at")},
    "user_topics": {"keys": ("user_id", "topic_id"), "fields": ("user_id", "topic_id")},
    "user_sources": {"keys": ("user_id", "source_id"), "fields": ("user_id", "source_id")},
    "user_tags": {"keys": ("user_id", "tag_id"), "fields": ("user_id", "tag_id")},
    "user_interests": {
        "keys": ("user_id", "topic_id"),
        "fields": ("user_id", "topic_id", "reason"),
    },
    "user_recommendations": {
        "keys": ("user_id", "article_id"),
        "fields": ("user_id", "article_id", "reason"),
    },
    "user_recommendation_states": {
        "keys": ("user_id",),
        "fields": ("user_id", "generation", "invalidated", "expires_at", "next_refresh_at"),
    },
    "topic_proposals": {"keys": ("id",), "fields": ("id", "status", "created_at", "reviewed_at")},
    "topic_relation_proposals": {"keys": ("id",), "fields": ("id", "status", "created_at")},
    "topic_decision_runs": {
        "keys": ("proposal_id",),
        "fields": ("proposal_id", "status", "reason", "updated_at"),
    },
    "topic_decision_calls": {
        "keys": ("proposal_id", "ordinal"),
        "fields": (
            "proposal_id",
            "ordinal",
            "stage_call",
            "started_at",
            "tokens",
            "charged_tokens",
            "escalated",
        ),
    },
    "inference_calls": {
        "keys": ("id",),
        "fields": (
            "id",
            "started_at",
            "finished_at",
            "status",
            "operation",
            "model",
            "reasoning_effort",
            "tokens",
            "web_searches",
            "duration_ms",
        ),
    },
    "article_reviews": {
        "keys": ("id",),
        "fields": ("id", "article_id", "created_at", "action", "automation"),
    },
    "ingestion_jobs": {
        "keys": ("id",),
        "fields": ("id", "status", "created_at", "available_at", "finished_at", "source_id"),
    },
    "article_enrichment_jobs": {
        "keys": ("id",),
        "fields": ("id", "status", "created_at", "available_at", "finished_at", "outcome"),
    },
    "article_image_jobs": {
        "keys": ("id",),
        "fields": (
            "id",
            "status",
            "created_at",
            "available_at",
            "finished_at",
            "outcome",
            "source_id",
            "topic_id",
        ),
    },
    "source_enrichment_jobs": {
        "keys": ("id",),
        "fields": ("id", "status", "created_at", "available_at", "finished_at", "source_id"),
    },
    "article_analysis_jobs": {
        "keys": ("id",),
        "fields": (
            "id",
            "status",
            "created_at",
            "available_at",
            "finished_at",
            "duration_ms",
            "outcome",
            "usage",
        ),
    },
    "topic_analysis_jobs": {
        "keys": ("id",),
        "fields": (
            "id",
            "status",
            "created_at",
            "available_at",
            "finished_at",
            "duration_ms",
            "outcome",
            "proposal_id",
            "topic_id",
            "usage",
        ),
    },
    "research_verification_jobs": {
        "keys": ("id",),
        "fields": (
            "id",
            "status",
            "created_at",
            "available_at",
            "finished_at",
            "duration_ms",
            "outcome",
            "usage",
        ),
    },
    "notification_deliveries": {
        "keys": ("id",),
        "fields": ("id", "status", "created_at", "available_at", "finished_at"),
    },
}


def _record(spec, table, alias):
    values = []
    for name in spec["fields"]:
        value = f'{alias}."{name}"'
        if table == "user_accounts" and name == "name":
            value = f"CASE WHEN nullif(btrim({value}), '') IS NOT NULL THEN 'set' END"
        elif table == "user_accounts" and name == "profile":
            value = (
                "jsonb_build_object('display_name', CASE WHEN "
                f"nullif(btrim({value}->>'display_name'), '') IS NOT NULL THEN 'set' END)"
            )
        elif name == "automation":
            value = (
                f"CASE WHEN {value} = '{{}}'::jsonb THEN '{{}}'::jsonb "
                "ELSE jsonb_build_object('set', true) END"
            )
        elif name in {"usage", "tokens"} and table != "topic_decision_calls":
            keys = (
                "totalTokens",
                "inputTokens",
                "cachedInputTokens",
                "outputTokens",
                "reasoningOutputTokens",
            )
            value = (
                "jsonb_build_object("
                + ", ".join(f"'{key}', {value}->'{key}'" for key in keys)
                + ")"
            )
        values.extend((f"'{name}'", value))
    return "jsonb_build_object(" + ", ".join(values) + ")"


def upgrade():
    op.execute("""CREATE TABLE analytics_export_state (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        enabled boolean NOT NULL DEFAULT false,
        capture_started_at timestamptz,
        last_success_at timestamptz,
        lease_token uuid, lease_until timestamptz, lease_generation bigint NOT NULL DEFAULT 0)""")
    op.execute("CREATE SEQUENCE analytics_export_generation AS bigint")
    op.execute("INSERT INTO analytics_export_state(singleton) VALUES (true)")
    op.execute("""CREATE TABLE analytics_export_cursors (
        source_table text PRIMARY KEY, cursor jsonb, complete boolean NOT NULL DEFAULT false)""")
    op.execute("""CREATE TABLE analytics_outbox (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source_table text NOT NULL, operation char(1) NOT NULL CHECK (operation IN ('I','D','T')),
        record jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
    op.execute("CREATE INDEX ix_analytics_outbox_created ON analytics_outbox(created_at, id)")
    op.execute("""CREATE FUNCTION analytics_capture_truncate() RETURNS trigger
        LANGUAGE plpgsql AS $body$ BEGIN
          IF EXISTS (SELECT 1 FROM analytics_export_state WHERE enabled) THEN
            INSERT INTO analytics_outbox(source_table, operation, record)
              VALUES (TG_TABLE_NAME, 'T', '{}'::jsonb);
          END IF;
          RETURN NULL;
        END $body$""")
    for table, spec in SOURCES.items():
        new = _record(spec, table, "NEW")
        old = _record(spec, table, "OLD")
        old_keys = ", ".join(f'OLD."{key}"' for key in spec["keys"])
        new_keys = ", ".join(f'NEW."{key}"' for key in spec["keys"])
        op.execute(f"""CREATE FUNCTION analytics_capture_{table}() RETURNS trigger
            LANGUAGE plpgsql AS $body$
            DECLARE before_record jsonb; after_record jsonb; operation char(1);
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM analytics_export_state WHERE enabled) THEN
                RETURN NULL;
              END IF;
              IF TG_OP <> 'INSERT' THEN before_record := {old}; END IF;
              IF TG_OP <> 'DELETE' THEN after_record := {new}; END IF;
              IF TG_OP = 'UPDATE' AND before_record IS NOT DISTINCT FROM after_record THEN
                RETURN NULL;
              END IF;
              IF TG_OP = 'UPDATE' AND ROW({old_keys}) IS DISTINCT FROM ROW({new_keys}) THEN
                INSERT INTO analytics_outbox(source_table, operation, record)
                  VALUES ('{table}', 'D', before_record);
              END IF;
              operation := CASE WHEN TG_OP = 'DELETE' THEN 'D' ELSE 'I' END;
              -- The existing scheduler explicitly marks retention cleanup. Real
              -- deletes/cascades continue to delete analytical current-state rows.
              IF TG_TABLE_NAME = 'article_opens' AND TG_OP = 'DELETE'
                AND current_setting('devfeed.analytics_retention_delete', true) = 'on' THEN
                operation := 'I';
              END IF;
              INSERT INTO analytics_outbox(source_table, operation, record)
                VALUES ('{table}', operation, coalesce(after_record, before_record));
              RETURN NULL;
            END $body$""")
        columns = ", ".join(f'"{name}"' for name in spec["fields"])
        op.execute(f"""CREATE TRIGGER analytics_capture
            AFTER INSERT OR UPDATE OF {columns} OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION analytics_capture_{table}()""")
        op.execute(f"""CREATE TRIGGER analytics_capture_truncate
            AFTER TRUNCATE ON {table} FOR EACH STATEMENT
            EXECUTE FUNCTION analytics_capture_truncate()""")
    # All historical inference aggregation belongs to ClickHouse. Keep the old
    # table as migration evidence; no application writes or reads its rollups.
    for event in ("insert", "update", "delete", "truncate"):
        op.execute(f"DROP TRIGGER inference_daily_usage_{event} ON inference_calls")


def downgrade():
    # A deliberate schema rollback rebuilds once before restoring transactional
    # maintenance. Ordinary application operation never runs this calculation.
    op.execute("LOCK TABLE inference_calls IN SHARE ROW EXCLUSIVE MODE")
    op.execute("TRUNCATE inference_daily_usage")
    op.execute("""INSERT INTO inference_daily_usage
        WITH parsed AS (
          SELECT *,
            CASE WHEN tokens->>'inputTokens' ~ '^[0-9]{1,12}$'
              THEN (tokens->>'inputTokens')::bigint ELSE 0 END AS input_tokens,
            CASE WHEN tokens->>'cachedInputTokens' ~ '^[0-9]{1,12}$'
              THEN (tokens->>'cachedInputTokens')::bigint ELSE 0 END AS cached_input_tokens,
            CASE WHEN tokens->>'outputTokens' ~ '^[0-9]{1,12}$'
              THEN (tokens->>'outputTokens')::bigint ELSE 0 END AS output_tokens,
            CASE WHEN tokens->>'reasoningOutputTokens' ~ '^[0-9]{1,12}$'
              THEN (tokens->>'reasoningOutputTokens')::bigint ELSE 0 END AS reasoning_tokens
          FROM inference_calls
        )
        SELECT (started_at AT TIME ZONE 'UTC')::date, operation,
          coalesce(model, 'Unknown model'), model IS NULL,
          coalesce(reasoning_effort, 'Inherited / unknown'), count(*),
          count(*) FILTER (WHERE status = 'returned'),
          count(*) FILTER (WHERE status = 'failed'),
          count(*) FILTER (WHERE status = 'running'),
          count(*) FILTER (WHERE input_tokens + output_tokens = 0),
          sum(input_tokens), sum(least(input_tokens, cached_input_tokens)),
          sum(output_tokens), sum(least(output_tokens, reasoning_tokens)),
          sum(web_searches::bigint), sum(duration_ms::bigint)
        FROM parsed GROUP BY 1,2,3,4,5""")
    for event, tables in (
        ("INSERT", "NEW TABLE AS new_rows"),
        ("DELETE", "OLD TABLE AS old_rows"),
        ("UPDATE", "OLD TABLE AS old_rows NEW TABLE AS new_rows"),
    ):
        op.execute(f"""CREATE TRIGGER inference_daily_usage_{event.lower()}
            AFTER {event} ON inference_calls REFERENCING {tables}
            FOR EACH STATEMENT EXECUTE FUNCTION inference_daily_usage_delta()""")
    op.execute("""CREATE TRIGGER inference_daily_usage_truncate AFTER TRUNCATE ON inference_calls
        FOR EACH STATEMENT EXECUTE FUNCTION inference_daily_usage_clear()""")
    for table in SOURCES:
        op.execute(f"DROP TRIGGER analytics_capture ON {table}")
        op.execute(f"DROP TRIGGER analytics_capture_truncate ON {table}")
        op.execute(f"DROP FUNCTION analytics_capture_{table}()")
    op.execute("DROP FUNCTION analytics_capture_truncate()")
    op.drop_table("analytics_outbox")
    op.drop_table("analytics_export_cursors")
    op.drop_table("analytics_export_state")
    op.execute("DROP SEQUENCE analytics_export_generation")
