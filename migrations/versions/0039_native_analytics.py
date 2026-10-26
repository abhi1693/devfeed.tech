"""Replace relational analytics mirroring with typed native fact streams."""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

OLD_SOURCES = (
    "articles",
    "sources",
    "topics",
    "article_topics",
    "article_origins",
    "user_accounts",
    "article_likes",
    "article_opens",
    "overview_daily",
    "user_topics",
    "user_sources",
    "user_tags",
    "user_interests",
    "user_recommendations",
    "user_recommendation_states",
    "topic_proposals",
    "topic_relation_proposals",
    "topic_decision_runs",
    "topic_decision_calls",
    "inference_calls",
    "article_reviews",
    "ingestion_jobs",
    "article_enrichment_jobs",
    "article_image_jobs",
    "source_enrichment_jobs",
    "article_analysis_jobs",
    "topic_analysis_jobs",
    "research_verification_jobs",
    "notification_deliveries",
)
SOURCES = {
    "articles": {
        "keys": ("id",),
        "fields": (
            "id",
            "discovered_at",
            "published_to_feed_at",
            "publication_status",
            "content_type",
        ),
    },
    "sources": {
        "keys": ("id",),
        "fields": ("id", "name", "approval_status", "enabled", "consecutive_failures"),
    },
    "topics": {"keys": ("id",), "fields": ("id", "name", "status")},
    "article_topics": {
        "keys": ("article_id", "topic_id"),
        "fields": ("article_id", "topic_id", "role"),
    },
    "article_origins": {"keys": ("id",), "fields": ("id", "article_id", "source_id")},
    "user_accounts": {"keys": ("id",), "fields": ("id", "created_at")},
    "article_opens": {
        "keys": ("article_id", "viewer_key", "opened_hour"),
        "fields": ("article_id", "viewer_key", "opened_hour"),
    },
    "user_topics": {"keys": ("user_id", "topic_id"), "fields": ("user_id", "topic_id")},
    "user_interests": {
        "keys": ("user_id", "topic_id"),
        "fields": ("user_id", "topic_id", "reason"),
    },
    "user_recommendation_states": {
        "keys": ("user_id",),
        "fields": (
            "user_id",
            "generation",
            "invalidated",
            "expires_at",
            "next_refresh_at",
            "interest_count",
        ),
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
            "stage",
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
            "job_id",
            "attempt",
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
        "fields": ("id", "status", "attempts", "created_at", "finished_at", "source_id"),
    },
    "article_enrichment_jobs": {
        "keys": ("id",),
        "fields": (
            "id",
            "status",
            "attempts",
            "created_at",
            "finished_at",
            "outcome",
            "article_id",
        ),
    },
    "article_image_jobs": {
        "keys": ("id",),
        "fields": (
            "id",
            "status",
            "attempts",
            "created_at",
            "finished_at",
            "outcome",
            "source_id",
            "article_id",
            "topic_id",
        ),
    },
    "source_enrichment_jobs": {
        "keys": ("id",),
        "fields": ("id", "status", "attempts", "created_at", "finished_at", "source_id"),
    },
    "article_analysis_jobs": {
        "keys": ("id",),
        "fields": (
            "id",
            "status",
            "attempts",
            "created_at",
            "finished_at",
            "duration_ms",
            "outcome",
            "article_id",
            "usage",
        ),
    },
    "topic_analysis_jobs": {
        "keys": ("id",),
        "fields": (
            "id",
            "status",
            "attempts",
            "created_at",
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
            "attempts",
            "created_at",
            "finished_at",
            "duration_ms",
            "outcome",
            "usage",
        ),
    },
    "notification_deliveries": {
        "keys": ("id",),
        "fields": ("id", "status", "attempts", "created_at", "finished_at"),
    },
}
BACKFILL_SOURCES = (
    "articles",
    "sources",
    "topics",
    "user_accounts",
    "article_opens",
    "user_topics",
    "user_interests",
    "user_recommendation_states",
    "topic_proposals",
    "topic_relation_proposals",
    "topic_decision_runs",
    "topic_decision_calls",
    "inference_calls",
    "ingestion_jobs",
    "article_enrichment_jobs",
    "article_image_jobs",
    "source_enrichment_jobs",
    "article_analysis_jobs",
    "topic_analysis_jobs",
    "research_verification_jobs",
    "notification_deliveries",
    "overview_daily",
)


def _record(spec, table, alias):
    values = []
    for name in spec["fields"]:
        value = f'{alias}."{name}"'
        if name == "automation":
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
                + ", ".join(
                    f"'{key}', CASE WHEN jsonb_typeof({value}->'{key}') "
                    f"IN ('string','number') THEN left({value}->>'{key}',19) END"
                    for key in keys
                )
                + ")"
            )
        values.extend((f"'{name}'", value))
    return "jsonb_build_object(" + ", ".join(values) + ")"


def upgrade():
    # Acquire every required relation lock without retaining partial locks while
    # waiting. Old source transactions and the V1 exporter finish before handoff.
    names = ", ".join(sorted(OLD_SOURCES))
    op.execute(f"""DO $body$
      DECLARE deadline timestamptz := clock_timestamp() + interval '30 seconds';
      BEGIN LOOP BEGIN
        LOCK TABLE {names} IN SHARE ROW EXCLUSIVE MODE NOWAIT;
        LOCK TABLE analytics_outbox, analytics_export_state, analytics_export_cursors
          IN ACCESS EXCLUSIVE MODE NOWAIT;
        EXIT;
      EXCEPTION WHEN lock_not_available THEN
        IF clock_timestamp() >= deadline THEN RAISE; END IF;
      END; PERFORM pg_sleep(0.05); END LOOP; END $body$""")
    for table in OLD_SOURCES:
        op.execute(f"DROP TRIGGER analytics_capture ON {table}")
        op.execute(f"DROP TRIGGER analytics_capture_truncate ON {table}")
        op.execute(f"DROP FUNCTION analytics_capture_{table}()")
    op.execute("DROP FUNCTION analytics_capture_truncate()")
    # Renaming both queue and state is an ownership fence. A still-running V1
    # worker cannot claim a lease, read V2 changes, or acknowledge their IDs.
    op.execute("ALTER TABLE analytics_outbox RENAME TO analytics_capture_events")
    op.execute("ALTER TABLE analytics_export_state RENAME TO analytics_pipeline_state")
    op.execute("ALTER TABLE analytics_export_cursors RENAME TO analytics_bootstrap_cursors")
    op.execute("ALTER TABLE analytics_capture_events ADD COLUMN before_record jsonb")
    # Existing V1 updates have no before image or attempt identity. Keep them
    # distinguishable for bounded normalization before their exact-ID ACK.
    op.execute(
        "ALTER TABLE analytics_capture_events ADD COLUMN format_version smallint "
        "NOT NULL DEFAULT 1 CHECK (format_version IN (1,2))"
    )
    op.execute("ALTER TABLE analytics_capture_events ALTER COLUMN format_version SET DEFAULT 2")
    op.execute(
        "ALTER TABLE analytics_pipeline_state ADD COLUMN native_import_complete "
        "boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE analytics_pipeline_state ADD COLUMN native_capture_started_at timestamptz"
    )
    op.execute("ALTER TABLE analytics_pipeline_state ADD COLUMN reader_coverage_start date")
    op.execute(
        "ALTER TABLE analytics_pipeline_state ADD COLUMN "
        "native_bootstrap_barrier boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE analytics_pipeline_state ADD COLUMN "
        "native_bootstrap_complete boolean NOT NULL DEFAULT false"
    )
    op.execute("UPDATE analytics_pipeline_state SET lease_token=NULL,lease_until=NULL")
    op.execute("TRUNCATE analytics_bootstrap_cursors")
    for source in BACKFILL_SOURCES:
        op.execute(f"INSERT INTO analytics_bootstrap_cursors(source_table) VALUES ('{source}')")
    op.execute("""CREATE TABLE analytics_fact_refresh (
        domain text NOT NULL, bucket timestamptz NOT NULL, generation bigint NOT NULL,
        requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        initial_pending boolean NOT NULL DEFAULT false, PRIMARY KEY(domain,bucket))""")
    op.execute(
        "CREATE INDEX ix_analytics_fact_refresh_requested ON "
        "analytics_fact_refresh(requested_at,domain,bucket)"
    )
    op.execute(
        "CREATE INDEX ix_analytics_fact_refresh_due ON "
        "analytics_fact_refresh(available_at,requested_at,domain,bucket)"
    )
    op.execute(
        "CREATE INDEX ix_analytics_fact_refresh_domain_due ON "
        "analytics_fact_refresh(domain,available_at,requested_at,bucket)"
    )
    op.execute("""CREATE TABLE analytics_rollup_compaction (
        table_name text NOT NULL, month integer NOT NULL, publishes integer NOT NULL DEFAULT 0,
        PRIMARY KEY(table_name,month))""")
    op.execute("""CREATE TABLE analytics_upgrade_checkpoint (
        name text PRIMARY KEY, cursor jsonb, complete boolean NOT NULL DEFAULT false)""")
    op.execute("""CREATE FUNCTION analytics_capture_truncate() RETURNS trigger
      LANGUAGE plpgsql AS $body$ BEGIN
        IF EXISTS (SELECT 1 FROM analytics_pipeline_state WHERE enabled) THEN
          INSERT INTO analytics_capture_events(source_table,operation,record)
            VALUES (TG_TABLE_NAME,'T','{}'::jsonb);
        END IF; RETURN NULL;
      END $body$""")
    for table, spec in SOURCES.items():
        old = _record(spec, table, "OLD")
        new = _record(spec, table, "NEW")
        op.execute(f"""CREATE FUNCTION analytics_capture_{table}() RETURNS trigger
          LANGUAGE plpgsql AS $body$
          DECLARE before_value jsonb; after_value jsonb; action char(1);
          BEGIN
            IF NOT EXISTS (SELECT 1 FROM analytics_pipeline_state WHERE enabled) THEN
              RETURN NULL;
            END IF;
            IF TG_OP <> 'INSERT' THEN before_value := {old}; END IF;
            IF TG_OP <> 'DELETE' THEN after_value := {new}; END IF;
            IF TG_OP = 'UPDATE' AND before_value IS NOT DISTINCT FROM after_value THEN
              RETURN NULL;
            END IF;
            action := CASE WHEN TG_OP = 'DELETE' THEN 'D' ELSE 'I' END;
            IF TG_TABLE_NAME = 'article_opens' AND TG_OP = 'DELETE'
              AND current_setting('devfeed.analytics_retention_delete',true)='on' THEN
              action := 'I'; after_value := before_value; before_value := NULL;
            END IF;
            INSERT INTO analytics_capture_events(source_table,operation,record,before_record)
              VALUES ('{table}',action,coalesce(after_value,before_value),before_value);
            RETURN NULL;
          END $body$""")
        columns = ", ".join(f'"{name}"' for name in spec["fields"])
        op.execute(f"""CREATE TRIGGER analytics_capture
          AFTER INSERT OR UPDATE OF {columns} OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION analytics_capture_{table}()""")
        op.execute(f"""CREATE TRIGGER analytics_capture_truncate
          AFTER TRUNCATE ON {table} FOR EACH STATEMENT
          EXECUTE FUNCTION analytics_capture_truncate()""")


def downgrade():
    # V1 source layouts cannot represent the native attempt/cohort observations.
    # Keep the captured queue and original data intact instead of discarding facts.
    raise RuntimeError("Native analytics capture cannot be downgraded; restore a verified backup")
