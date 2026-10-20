"""Durable pipeline projections, report snapshots, and transactional change capture."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

# UPDATE OF avoids reading or serializing private evidence for lease heartbeats.
# Candidate/source snapshot tokens include all their columns, so every update matters.
TRIGGERS = {
    "articles": (
        "article_entity",
        "id,canonical_url,title,summary,ai_title,ai_summary,ai_description,review_status,"
        "publication_status,language,content_type,content_format,classification_provenance,"
        "metadata_source_type,published_at,image_url,managed_image,discovered_at,"
        "editorial_revision,automation_next_check_at,published_to_feed_at",
    ),
    "sources": ("source_entity", None),
    "source_candidates": ("candidate", None),
    "article_contents": ("article_owner", None),
    "article_origins": ("article_owner", None),
    "article_topics": ("article_owner", None),
    "article_reviews": ("article_owner", None),
    "article_enrichment_jobs": (
        "article_owner",
        "id,article_id,status,attempts,available_at,created_at,finished_at,outcome,error,result",
    ),
    "article_analysis_jobs": (
        "article_owner",
        "id,article_id,status,attempts,available_at,created_at,finished_at,outcome,error,"
        "input_hash,input_snapshot,catalog_hash,catalog_snapshot,prompt_version,result,usage,"
        "inputs_pruned_at",
    ),
    "article_image_jobs": (
        "image",
        "id,article_id,source_id,status,attempts,available_at,created_at,finished_at,outcome,"
        "error,operation,image_url,storage,storage_version",
    ),
    "source_enrichment_jobs": ("source_owner", None),
    "ingestion_jobs": ("source_owner", None),
    "source_reviews": ("source_owner", None),
    "source_discovery_jobs": ("discovery", None),
}


def upgrade():
    # Store only the identities/cutoffs needed for recurring publication reports;
    # JSON evidence is evaluated once at index maintenance, never during a report.
    op.create_index(
        "ix_article_reviews_manual_publication",
        "article_reviews",
        ["article_id", "created_at"],
        postgresql_where=sa.text("automation = '{}'::jsonb"),
    )
    op.create_index(
        "ix_article_reviews_automatic_publication",
        "article_reviews",
        ["article_id", "created_at"],
        postgresql_where=sa.text("action = 'publish' AND automation <> '{}'::jsonb"),
    )
    op.create_table(
        "pipeline_projections",
        sa.Column("policy_version", sa.String(100), primary_key=True),
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("entity_id", sa.Uuid(), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("source_id", sa.Uuid()),
        sa.Column("source_name", sa.String(200)),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("has_active_work", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("next_transition_at", sa.DateTime(timezone=True)),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('article','source','candidate')", name="ck_pipeline_projection_kind"
        ),
    )
    op.create_index(
        "ix_pipeline_projection_lane",
        "pipeline_projections",
        [
            "policy_version",
            "kind",
            "stage",
            sa.text("(CASE WHEN state = 'running' THEN 0 ELSE 1 END)"),
            "created_at",
            "entity_id",
        ],
    )
    op.create_index(
        "ix_pipeline_projection_source",
        "pipeline_projections",
        ["policy_version", "kind", "source_id", "stage", "state"],
    )
    op.create_index(
        "ix_pipeline_projection_terminal",
        "pipeline_projections",
        ["policy_version", "kind", "stage", sa.text("terminal_at DESC"), "entity_id"],
    )
    op.create_index(
        "ix_pipeline_projection_transition",
        "pipeline_projections",
        ["policy_version", "next_transition_at", "kind", "entity_id"],
        postgresql_where=sa.text("next_transition_at IS NOT NULL"),
    )
    op.create_table(
        "read_model_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Uuid()),
        sa.Column("after_id", sa.Uuid()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind IN ('article','source','candidate','catalog','policy',"
            "'backfill_article','backfill_source','backfill_candidate','source_fanout')",
            name="ck_read_model_event_kind",
        ),
        sa.CheckConstraint(
            "entity_id IS NOT NULL OR kind IN ('catalog','policy',"
            "'backfill_article','backfill_source','backfill_candidate')",
            name="ck_read_model_event_entity",
        ),
    )
    op.create_index("ix_read_model_event_created", "read_model_events", ["created_at", "id"])
    op.create_table(
        "read_model_states",
        sa.Column("name", sa.String(100), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "report_snapshots",
        sa.Column("group", sa.String(50), primary_key=True),
        sa.Column("days", sa.Integer(), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.CheckConstraint("days BETWEEN 1 AND 90", name="ck_report_snapshot_days"),
    )
    op.create_table(
        "report_refreshes",
        sa.Column("group", sa.String(50), primary_key=True),
        sa.Column("days", sa.Integer(), primary_key=True),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(100)),
        sa.CheckConstraint("days BETWEEN 1 AND 90", name="ck_report_refresh_days"),
        sa.CheckConstraint("attempts >= 0", name="ck_report_refresh_attempts"),
    )
    op.create_index("ix_report_refresh_due", "report_refreshes", ["available_at", "group", "days"])
    op.execute("""
    CREATE FUNCTION devfeed_read_model_enqueue(kind text, identifier uuid)
    RETURNS void LANGUAGE sql AS $$
      INSERT INTO read_model_events(kind, entity_id)
      SELECT kind, identifier WHERE identifier IS NOT NULL OR kind IN ('catalog','policy')
    $$
    """)
    op.execute("""
    CREATE FUNCTION devfeed_read_model_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
      mode text := TG_ARGV[0];
      event_kind text;
      previous_id uuid; current_id uuid;
      previous_source uuid; current_source uuid;
    BEGIN
      IF mode IN ('article_entity','source_entity','candidate') THEN
        event_kind := CASE mode WHEN 'article_entity' THEN 'article'
                               WHEN 'source_entity' THEN 'source' ELSE 'candidate' END;
        IF TG_OP <> 'INSERT' THEN previous_id := OLD.id; END IF;
        IF TG_OP <> 'DELETE' THEN current_id := NEW.id; END IF;
        IF mode = 'candidate' THEN
          IF TG_OP <> 'INSERT' THEN previous_source := OLD.source_id; END IF;
          IF TG_OP <> 'DELETE' THEN current_source := NEW.source_id; END IF;
        END IF;
      ELSIF mode IN ('article_owner','image') THEN
        event_kind := 'article';
        IF TG_OP <> 'INSERT' THEN previous_id := OLD.article_id; END IF;
        IF TG_OP <> 'DELETE' THEN current_id := NEW.article_id; END IF;
        IF mode = 'image' THEN
          IF TG_OP <> 'INSERT' THEN previous_source := OLD.source_id; END IF;
          IF TG_OP <> 'DELETE' THEN current_source := NEW.source_id; END IF;
        END IF;
      ELSIF mode = 'source_owner' THEN
        event_kind := 'source';
        IF TG_OP <> 'INSERT' THEN previous_id := OLD.source_id; END IF;
        IF TG_OP <> 'DELETE' THEN current_id := NEW.source_id; END IF;
      ELSIF mode = 'discovery' THEN
        event_kind := 'candidate';
        IF TG_OP <> 'INSERT' THEN
          previous_id := OLD.candidate_id;
          SELECT source_id INTO previous_source FROM source_candidates WHERE id = previous_id;
        END IF;
        IF TG_OP <> 'DELETE' THEN
          current_id := NEW.candidate_id;
          SELECT source_id INTO current_source FROM source_candidates WHERE id = current_id;
        END IF;
      END IF;
      IF previous_id IS DISTINCT FROM current_id THEN
        PERFORM devfeed_read_model_enqueue(event_kind, previous_id);
      END IF;
      PERFORM devfeed_read_model_enqueue(event_kind, current_id);
      IF previous_source IS DISTINCT FROM current_source THEN
        PERFORM devfeed_read_model_enqueue('source', previous_source);
      END IF;
      PERFORM devfeed_read_model_enqueue('source', current_source);
      IF mode = 'source_entity' AND TG_OP = 'UPDATE' THEN
        IF OLD.name IS DISTINCT FROM NEW.name
           OR OLD.approval_status IS DISTINCT FROM NEW.approval_status
           OR OLD.enabled IS DISTINCT FROM NEW.enabled THEN
          PERFORM devfeed_read_model_enqueue('source_fanout', current_id);
        END IF;
      END IF;
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$
    """)
    op.execute("""
    CREATE FUNCTION devfeed_read_model_rebuild() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM devfeed_read_model_enqueue(TG_ARGV[0], NULL);
      RETURN NULL;
    END $$
    """)
    for table, (mode, columns) in TRIGGERS.items():
        update = f"UPDATE OF {columns}" if columns else "UPDATE"
        op.execute(f"""CREATE TRIGGER read_model_changed
          AFTER INSERT OR {update} OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION devfeed_read_model_changed('{mode}')""")
        op.execute(f"""CREATE TRIGGER read_model_truncated AFTER TRUNCATE ON {table}
          FOR EACH STATEMENT EXECUTE FUNCTION devfeed_read_model_rebuild('policy')""")
    # Catalog revision triggers already cover bulk SQL, deletes, and ranking fields.
    op.execute("""CREATE TRIGGER read_model_catalog_changed
      AFTER INSERT OR UPDATE ON catalog_revisions
      FOR EACH ROW WHEN (NEW.name IN ('topics','tags'))
      EXECUTE FUNCTION devfeed_read_model_rebuild('catalog')""")
    op.execute("""CREATE TRIGGER read_model_catalog_truncated AFTER TRUNCATE ON catalog_revisions
      FOR EACH STATEMENT EXECUTE FUNCTION devfeed_read_model_rebuild('catalog')""")
    # Backfill is expanded by the projector in keyset batches, never in the migration.
    op.execute("INSERT INTO read_model_events(kind) VALUES ('policy')")


def downgrade():
    op.drop_index("ix_article_reviews_automatic_publication", table_name="article_reviews")
    op.drop_index("ix_article_reviews_manual_publication", table_name="article_reviews")
    for table in TRIGGERS:
        op.execute(f"DROP TRIGGER read_model_truncated ON {table}")
        op.execute(f"DROP TRIGGER read_model_changed ON {table}")
    op.execute("DROP TRIGGER read_model_catalog_truncated ON catalog_revisions")
    op.execute("DROP TRIGGER read_model_catalog_changed ON catalog_revisions")
    op.execute("DROP FUNCTION devfeed_read_model_rebuild()")
    op.execute("DROP FUNCTION devfeed_read_model_changed()")
    op.execute("DROP FUNCTION devfeed_read_model_enqueue(text, uuid)")
    for table in (
        "report_refreshes",
        "report_snapshots",
        "read_model_states",
        "read_model_events",
        "pipeline_projections",
    ):
        op.drop_table(table)
