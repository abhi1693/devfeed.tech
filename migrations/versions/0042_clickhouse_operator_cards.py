"""Serve current administration state in ClickHouse; retire PostgreSQL projections.

The queued native observations, identities and source/history cursors survive.
Only obsolete, derived PostgreSQL cards/reports and their invalidation layer retire.
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

# Frozen capture contract: future application fields must not change this migration.
TOKEN_FIELDS = (
    "totalTokens",
    "inputTokens",
    "cachedInputTokens",
    "outputTokens",
    "reasoningOutputTokens",
)

SOURCES = {
    "articles": {
        "fields": [
            "id",
            "discovered_at",
            "published_to_feed_at",
            "publication_status",
            "content_type",
        ],
        "keys": ["id"],
    },
    "sources": {
        "fields": ["id", "name", "approval_status", "enabled", "consecutive_failures"],
        "keys": ["id"],
    },
    "topics": {"fields": ["id", "name", "status"], "keys": ["id"]},
    "article_topics": {
        "fields": ["article_id", "topic_id", "role"],
        "keys": ["article_id", "topic_id"],
    },
    "article_origins": {"fields": ["id", "article_id", "source_id"], "keys": ["id"]},
    "user_accounts": {"fields": ["id", "created_at"], "keys": ["id"]},
    "article_opens": {
        "fields": ["article_id", "viewer_key", "opened_hour"],
        "keys": ["article_id", "viewer_key", "opened_hour"],
    },
    "user_topics": {"fields": ["user_id", "topic_id"], "keys": ["user_id", "topic_id"]},
    "user_interests": {
        "fields": ["user_id", "topic_id", "reason"],
        "keys": ["user_id", "topic_id"],
    },
    "user_recommendation_states": {
        "fields": [
            "user_id",
            "generation",
            "invalidated",
            "expires_at",
            "next_refresh_at",
            "interest_count",
        ],
        "keys": ["user_id"],
    },
    "topic_proposals": {"fields": ["id", "status", "created_at", "reviewed_at"], "keys": ["id"]},
    "topic_relation_proposals": {"fields": ["id", "status", "created_at"], "keys": ["id"]},
    "topic_decision_runs": {
        "fields": ["proposal_id", "status", "reason", "updated_at"],
        "keys": ["proposal_id"],
    },
    "topic_decision_calls": {
        "fields": [
            "proposal_id",
            "ordinal",
            "stage",
            "stage_call",
            "started_at",
            "tokens",
            "charged_tokens",
            "escalated",
        ],
        "keys": ["proposal_id", "ordinal"],
    },
    "inference_calls": {
        "fields": [
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
        ],
        "keys": ["id"],
    },
    "article_reviews": {
        "fields": ["id", "article_id", "created_at", "action", "automation"],
        "keys": ["id"],
    },
    "source_candidates": {
        "fields": [
            "id",
            "status",
            "approval_status",
            "selected_feed_id",
            "source_id",
            "created_at",
            "updated_at",
        ],
        "keys": ["id"],
    },
    "article_contents": {
        "fields": ["article_id", "content_hash", "method", "retrieved_at"],
        "keys": ["article_id"],
    },
    "source_reviews": {"fields": ["id", "source_id", "decision", "created_at"], "keys": ["id"]},
    "source_discovery_jobs": {
        "fields": [
            "id",
            "candidate_id",
            "stage",
            "status",
            "attempts",
            "available_at",
            "finished_at",
        ],
        "keys": ["id"],
    },
    "catalog_revisions": {"fields": ["name", "revision"], "keys": ["name"]},
    "ingestion_jobs": {
        "fields": [
            "id",
            "status",
            "attempts",
            "created_at",
            "finished_at",
            "available_at",
            "source_id",
        ],
        "keys": ["id"],
    },
    "article_enrichment_jobs": {
        "fields": [
            "id",
            "status",
            "attempts",
            "created_at",
            "finished_at",
            "available_at",
            "outcome",
            "article_id",
        ],
        "keys": ["id"],
    },
    "article_image_jobs": {
        "fields": [
            "id",
            "status",
            "attempts",
            "created_at",
            "finished_at",
            "available_at",
            "outcome",
            "source_id",
            "article_id",
            "topic_id",
        ],
        "keys": ["id"],
    },
    "source_enrichment_jobs": {
        "fields": [
            "id",
            "status",
            "attempts",
            "created_at",
            "finished_at",
            "available_at",
            "source_id",
        ],
        "keys": ["id"],
    },
    "article_analysis_jobs": {
        "fields": [
            "id",
            "status",
            "attempts",
            "created_at",
            "finished_at",
            "available_at",
            "duration_ms",
            "outcome",
            "article_id",
            "usage",
        ],
        "keys": ["id"],
    },
    "topic_analysis_jobs": {
        "fields": [
            "id",
            "status",
            "attempts",
            "created_at",
            "finished_at",
            "available_at",
            "duration_ms",
            "outcome",
            "proposal_id",
            "topic_id",
            "usage",
        ],
        "keys": ["id"],
    },
    "research_verification_jobs": {
        "fields": [
            "id",
            "status",
            "attempts",
            "created_at",
            "finished_at",
            "available_at",
            "duration_ms",
            "outcome",
            "relationships",
            "usage",
        ],
        "keys": ["id"],
    },
    "notification_deliveries": {
        "fields": ["id", "status", "attempts", "created_at", "finished_at", "available_at"],
        "keys": ["id"],
    },
}

WATCH_FIELDS = {
    "articles": [
        "id",
        "canonical_url",
        "url_hash",
        "title",
        "slug",
        "summary",
        "matching_tokens",
        "ai_title",
        "ai_summary",
        "ai_description",
        "review_status",
        "publication_status",
        "published_to_feed_at",
        "editorial_revision",
        "classification_provenance",
        "sponsor_names",
        "author",
        "image_url",
        "managed_image",
        "language",
        "content_type",
        "content_format",
        "published_at",
        "feed_at",
        "metadata_source_type",
        "discovered_at",
        "automation_started_at",
    ],
    "sources": [
        "id",
        "name",
        "slug",
        "feed_url",
        "source_type",
        "description",
        "website_url",
        "logo_url",
        "managed_logo",
        "image_url",
        "language",
        "relevance_assessment",
        "processing_request",
        "submitted_by",
        "submission_channel",
        "approval_status",
        "reviewed_at",
        "reviewed_by",
        "review_note",
        "metadata_enriched_at",
        "metadata_error",
        "updated_at",
        "enabled",
        "publication_policy",
        "publication_policy_revision",
        "polling_mode",
        "polling_state",
        "scheduled_polling_mode",
        "scheduled_interval_seconds",
        "poll_interval_seconds",
        "etag",
        "last_modified",
        "next_fetch_at",
        "last_attempt_at",
        "last_success_at",
        "consecutive_failures",
        "last_error",
        "created_at",
    ],
    "topics": [
        "id",
        "name",
        "slug",
        "kind",
        "aliases",
        "identity_keys",
        "matching_tokens",
        "keywords",
        "status",
        "description",
        "ai_description",
        "website_url",
        "logo_url",
        "managed_logo",
        "facts",
        "created_at",
        "updated_at",
    ],
    "article_topics": ["article_id", "topic_id", "role", "relevance", "evidence", "origin"],
    "article_origins": [
        "id",
        "article_id",
        "source_id",
        "entry_key",
        "original_url",
        "source_metadata",
    ],
    "user_accounts": ["id", "created_at"],
    "article_opens": ["article_id", "viewer_key", "opened_hour"],
    "user_topics": ["user_id", "topic_id"],
    "user_interests": ["user_id", "topic_id", "reason"],
    "user_recommendation_states": [
        "user_id",
        "generation",
        "invalidated",
        "expires_at",
        "next_refresh_at",
        "interest_count",
    ],
    "topic_proposals": [
        "id",
        "batch_id",
        "topic_id",
        "slug",
        "action",
        "origin",
        "source_name",
        "proposed",
        "baseline",
        "evidence",
        "status",
        "created_at",
        "created_by",
        "reviewed_at",
        "reviewed_by",
        "review_note",
        "applied",
        "research_requested",
    ],
    "topic_relation_proposals": [
        "id",
        "job_id",
        "topic_id",
        "related_topic_id",
        "relation",
        "explanation",
        "evidence_url",
        "evidence_title",
        "evidence_quote",
        "topic_snapshot",
        "related_topic_snapshot",
        "status",
        "created_at",
        "created_by",
        "reviewed_at",
        "reviewed_by",
        "review_note",
    ],
    "topic_decision_runs": [
        "proposal_id",
        "input_hash",
        "status",
        "reason",
        "state",
        "created_at",
        "updated_at",
    ],
    "topic_decision_calls": [
        "proposal_id",
        "ordinal",
        "stage",
        "stage_call",
        "started_at",
        "tokens",
        "charged_tokens",
        "escalated",
    ],
    "inference_calls": [
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
    ],
    "article_reviews": [
        "id",
        "article_id",
        "action",
        "actor",
        "note",
        "revision",
        "automation",
        "created_at",
    ],
    "source_candidates": [
        "id",
        "identity_url",
        "name",
        "status",
        "approval_status",
        "selected_feed_id",
        "source_id",
        "review",
        "created_at",
        "updated_at",
    ],
    "article_contents": ["article_id", "url", "text", "content_hash", "method", "retrieved_at"],
    "source_reviews": ["id", "source_id", "decision", "actor", "note", "created_at"],
    "source_discovery_jobs": [
        "id",
        "candidate_id",
        "stage",
        "error",
        "result",
        "status",
        "attempts",
        "available_at",
        "dispatched_at",
        "lease_until",
        "lease_token",
        "created_at",
        "finished_at",
    ],
    "catalog_revisions": ["name", "revision"],
    "ingestion_jobs": [
        "id",
        "source_id",
        "http_status",
        "automatic",
        "new_source_entries",
        "entries_seen",
        "articles_created",
        "entries_skipped",
        "error",
        "status",
        "attempts",
        "available_at",
        "dispatched_at",
        "lease_until",
        "lease_token",
        "created_at",
        "finished_at",
    ],
    "article_enrichment_jobs": [
        "id",
        "article_id",
        "http_status",
        "outcome",
        "changed_fields",
        "result",
        "error",
        "requires_solver",
        "status",
        "attempts",
        "available_at",
        "dispatched_at",
        "lease_until",
        "lease_token",
        "created_at",
        "finished_at",
    ],
    "article_image_jobs": [
        "requires_solver",
        "operation",
        "storage",
        "id",
        "article_id",
        "topic_id",
        "source_id",
        "product_id",
        "account_id",
        "subject_revision",
        "storage_version",
        "http_status",
        "outcome",
        "image_url",
        "method",
        "error",
        "status",
        "attempts",
        "available_at",
        "dispatched_at",
        "lease_until",
        "lease_token",
        "created_at",
        "finished_at",
    ],
    "source_enrichment_jobs": [
        "id",
        "source_id",
        "error",
        "operation",
        "result",
        "changed_fields",
        "requires_solver",
        "status",
        "attempts",
        "available_at",
        "dispatched_at",
        "lease_until",
        "lease_token",
        "created_at",
        "finished_at",
    ],
    "article_analysis_jobs": [
        "id",
        "article_id",
        "input_hash",
        "input_snapshot",
        "catalog_snapshot",
        "catalog_hash",
        "editorial_revision",
        "model",
        "prompt_version",
        "result",
        "outcome",
        "error",
        "usage",
        "duration_ms",
        "inputs_pruned_at",
        "status",
        "attempts",
        "available_at",
        "dispatched_at",
        "lease_until",
        "lease_token",
        "created_at",
        "finished_at",
    ],
    "topic_analysis_jobs": [
        "id",
        "proposal_id",
        "topic_id",
        "input_hash",
        "input_snapshot",
        "requested_by",
        "model",
        "prompt_version",
        "result",
        "outcome",
        "error",
        "usage",
        "duration_ms",
        "inputs_pruned_at",
        "status",
        "attempts",
        "available_at",
        "dispatched_at",
        "lease_until",
        "lease_token",
        "created_at",
        "finished_at",
    ],
    "research_verification_jobs": [
        "id",
        "relationships",
        "outcome",
        "error",
        "usage",
        "duration_ms",
        "status",
        "attempts",
        "available_at",
        "dispatched_at",
        "lease_until",
        "lease_token",
        "created_at",
        "finished_at",
    ],
    "notification_deliveries": [
        "id",
        "status",
        "attempts",
        "created_at",
        "finished_at",
        "available_at",
    ],
}

LEGACY_SOURCES = (
    "articles",
    "sources",
    "source_candidates",
    "article_contents",
    "article_origins",
    "article_topics",
    "article_reviews",
    "article_enrichment_jobs",
    "article_analysis_jobs",
    "article_image_jobs",
    "source_enrichment_jobs",
    "ingestion_jobs",
    "source_reviews",
    "source_discovery_jobs",
)


def _different(fields):
    return " OR ".join(f'OLD."{name}" IS DISTINCT FROM NEW."{name}"' for name in fields)


def _record(table, fields, alias):
    values = []
    for name in fields:
        value = f'{alias}."{name}"'
        if name == "automation":
            value = (
                f"CASE WHEN {value} = '{{}}'::jsonb THEN '{{}}'::jsonb "
                "ELSE jsonb_build_object('set', true) END"
            )
        elif name in {"usage", "tokens"} and table != "topic_decision_calls":
            value = (
                "jsonb_build_object("
                + ", ".join(
                    f"'{key}', CASE WHEN jsonb_typeof({value}->'{key}') "
                    f"IN ('string','number') THEN left({value}->>'{key}',19) END"
                    for key in TOKEN_FIELDS
                )
                + ")"
            )
        values.extend((f"'{name}'", value))
    return "jsonb_build_object(" + ", ".join(values) + ")"


def capture_statements(sources, watch_fields):
    """Return complete V3 trigger DDL for a source schema, without executing it."""
    result = []
    for table in sources:
        result.extend(
            (
                f"DROP TRIGGER IF EXISTS analytics_capture ON {table}",
                f"DROP TRIGGER IF EXISTS analytics_capture_truncate ON {table}",
                f"DROP FUNCTION IF EXISTS analytics_capture_{table}()",
            )
        )
    result.extend(
        (
            "DROP FUNCTION IF EXISTS analytics_capture_truncate()",
            """CREATE FUNCTION analytics_capture_truncate() RETURNS trigger
              LANGUAGE plpgsql AS $body$ BEGIN
                IF EXISTS (SELECT 1 FROM analytics_pipeline_state WHERE enabled) THEN
                  INSERT INTO analytics_ingest_events(source_table,operation,record)
                    VALUES (TG_TABLE_NAME,'T','{}'::jsonb);
                END IF;
                RETURN NULL;
              END $body$""",
        )
    )
    for table, spec in sources.items():
        before = _record(table, spec["fields"], "OLD")
        after = _record(table, spec["fields"], "NEW")
        changed = _different(watch_fields[table])
        result.append(f"""CREATE FUNCTION analytics_capture_{table}() RETURNS trigger
          LANGUAGE plpgsql AS $body$
          DECLARE before_value jsonb; after_value jsonb; action char(1);
          BEGIN
            IF NOT EXISTS (SELECT 1 FROM analytics_pipeline_state WHERE enabled) THEN
              RETURN NULL;
            END IF;
            IF TG_OP = 'UPDATE' AND NOT ({changed}) THEN RETURN NULL; END IF;
            IF TG_OP <> 'INSERT' THEN before_value := {before}; END IF;
            IF TG_OP <> 'DELETE' THEN after_value := {after}; END IF;
            action := CASE WHEN TG_OP='DELETE' THEN 'D' ELSE 'I' END;
            IF TG_TABLE_NAME='article_opens' AND TG_OP='DELETE'
              AND current_setting('devfeed.analytics_retention_delete',true)='on' THEN
              action := 'I'; after_value := before_value; before_value := NULL;
            END IF;
            INSERT INTO analytics_ingest_events(source_table,operation,record,before_record)
              VALUES ('{table}',action,coalesce(after_value,before_value),before_value);
            RETURN NULL;
          END $body$""")
        columns = ", ".join(f'"{name}"' for name in watch_fields[table])
        result.extend(
            (
                f"CREATE TRIGGER analytics_capture AFTER INSERT OR UPDATE OF {columns} "
                f"OR DELETE ON {table} FOR EACH ROW "
                f"EXECUTE FUNCTION analytics_capture_{table}()",
                f"CREATE TRIGGER analytics_capture_truncate AFTER TRUNCATE ON {table} "
                "FOR EACH STATEMENT EXECUTE FUNCTION analytics_capture_truncate()",
            )
        )
    return tuple(result)


def upgrade():
    # Release partial locks on retry. Queue renaming and capture replacement must
    # be atomic with source writes; old publishers/exporters are stopped first.
    sources = ", ".join(sorted(SOURCES))
    op.execute(f"""DO $body$
      DECLARE deadline timestamptz := clock_timestamp()+interval '30 seconds';
      BEGIN LOOP BEGIN
        LOCK TABLE {sources} IN SHARE ROW EXCLUSIVE MODE NOWAIT;
        LOCK TABLE analytics_capture_events,analytics_pipeline_state,
          pipeline_projections,read_model_events,read_model_states,
          report_snapshots,report_refreshes IN ACCESS EXCLUSIVE MODE NOWAIT;
        EXIT;
      EXCEPTION WHEN lock_not_available THEN
        IF clock_timestamp()>=deadline THEN RAISE; END IF;
      END; PERFORM pg_sleep(0.05); END LOOP; END $body$""")
    for table in LEGACY_SOURCES:
        op.execute(f"DROP TRIGGER read_model_changed ON {table}")
        op.execute(f"DROP TRIGGER read_model_truncated ON {table}")
    op.execute("DROP TRIGGER read_model_article_image_changed ON article_image_jobs")
    op.execute("DROP TRIGGER read_model_source_image_changed ON article_image_jobs")
    op.execute("DROP TRIGGER read_model_catalog_changed ON catalog_revisions")
    op.execute("DROP TRIGGER read_model_catalog_truncated ON catalog_revisions")
    op.execute("DROP TRIGGER read_model_article_clock_changed ON articles")
    for name, arguments in (
        ("devfeed_read_model_article_clock", ""),
        ("devfeed_read_model_changed", ""),
        ("devfeed_read_model_rebuild", ""),
        ("devfeed_read_model_enqueue", "text,uuid"),
    ):
        op.execute(f"DROP FUNCTION {name}({arguments})")
    # Existing V1/V2 rows and their original identity sequence are retained. A
    # stale exporter cannot silently ACK new current-card-only observation kinds.
    op.execute("ALTER TABLE analytics_capture_events RENAME TO analytics_ingest_events")
    op.execute(
        "ALTER TABLE analytics_ingest_events "
        "DROP CONSTRAINT analytics_capture_events_format_version_check"
    )
    op.execute(
        "ALTER TABLE analytics_ingest_events "
        "ADD CONSTRAINT ck_analytics_ingest_format_version "
        "CHECK (format_version IN (1,2,3))"
    )
    op.execute("ALTER TABLE analytics_ingest_events ALTER COLUMN format_version SET DEFAULT 3")
    # This ownership handoff fences an in-flight binary even if it already read
    # its old queue before the relation rename. Historical completion stays intact.
    op.execute("UPDATE analytics_pipeline_state SET lease_token=NULL,lease_until=NULL")
    op.execute("""ALTER TABLE analytics_pipeline_state
        ADD COLUMN card_target_policy text,
        ADD COLUMN card_serving_policy text,
        ADD COLUMN card_bootstrap_complete boolean NOT NULL DEFAULT false,
        ADD COLUMN card_bootstrap_barrier boolean NOT NULL DEFAULT false,
        ADD COLUMN card_catalog_revision jsonb,
        ADD COLUMN card_catalog_target_revision jsonb,
        ADD COLUMN card_catalog_progress_at timestamptz""")
    kinds = "'article','source','candidate','topic-proposal','relationship-proposal'"
    op.execute(f"""CREATE TABLE analytics_card_refresh (
        kind text NOT NULL,entity_id uuid NOT NULL,generation bigint NOT NULL,
        requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        initial_pending boolean NOT NULL DEFAULT false,
        PRIMARY KEY(kind,entity_id),
        CONSTRAINT ck_analytics_card_refresh_kind CHECK (kind IN ({kinds})))""")
    op.execute(
        "CREATE INDEX ix_analytics_card_refresh_due "
        "ON analytics_card_refresh(available_at,kind,entity_id)"
    )
    op.execute(f"""CREATE TABLE analytics_card_backfills (
        kind text PRIMARY KEY,cursor uuid,complete boolean NOT NULL DEFAULT false,
        CONSTRAINT ck_analytics_card_backfill_kind CHECK (kind IN ({kinds})))""")
    op.execute("""CREATE TABLE analytics_card_fanout (
        kind text NOT NULL,entity_id uuid NOT NULL,cursor uuid,generation bigint NOT NULL,
        requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        initial_pending boolean NOT NULL DEFAULT false,
        PRIMARY KEY(kind,entity_id),
        CONSTRAINT ck_analytics_card_fanout_kind CHECK (kind IN ('source','catalog')))""")
    op.execute(
        "CREATE INDEX ix_analytics_card_fanout_due "
        "ON analytics_card_fanout(available_at,kind,entity_id)"
    )
    for statement in capture_statements(SOURCES, WATCH_FIELDS):
        op.execute(statement)
    for name in (
        "report_refreshes",
        "report_snapshots",
        "read_model_states",
        "read_model_events",
        "pipeline_projections",
    ):
        op.drop_table(name)


def downgrade():
    raise RuntimeError(
        "ClickHouse operator state requires a forward repair or coordinated backup restore"
    )
