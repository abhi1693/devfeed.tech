"""Wake article automation from semantic changes, with bounded catalog/source fanout."""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

ARTICLE_COLUMNS = (
    "id,canonical_url,title,summary,ai_title,ai_summary,ai_description,review_status,"
    "publication_status,language,content_type,content_format,classification_provenance,"
    "metadata_source_type,published_at,discovered_at,image_url,managed_image,editorial_revision"
)
CHILD_COLUMNS = {
    "article_contents": "article_id,url,text,content_hash,method",
    "article_origins": "article_id,source_id,source_metadata",
    "article_topics": "article_id,topic_id,role,relevance,evidence,origin",
    "article_tags": "article_id,tag_id,origin",
    "article_reviews": "article_id,action,revision,automation",
    "article_enrichment_jobs": "article_id,status,attempts,available_at,outcome,error,result",
    "article_analysis_jobs": (
        "article_id,status,attempts,available_at,outcome,error,input_hash,catalog_hash,"
        "prompt_version,result,editorial_revision"
    ),
    "article_image_jobs": (
        "article_id,status,attempts,available_at,outcome,error,operation,image_url,storage,"
        "storage_version"
    ),
}
SOURCE_COLUMNS = "enabled,approval_status,publication_policy,publication_policy_revision"


def _different(columns):
    return (
        "ROW("
        + ",".join(f"OLD.{name}" for name in columns.split(","))
        + ") IS DISTINCT FROM ROW("
        + ",".join(f"NEW.{name}" for name in columns.split(","))
        + ")"
    )


def upgrade():
    op.add_column(
        "articles",
        sa.Column("automation_input_revision", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.add_column(
        "articles",
        sa.Column(
            "automation_evaluated_revision", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    # PostgreSQL's constant missing value classifies existing rows without a
    # population UPDATE. Future inserts remain foreground after dropping default.
    op.add_column(
        "articles", sa.Column("automation_wait_reason", sa.String(50), server_default="maintenance")
    )
    op.alter_column("articles", "automation_wait_reason", server_default=None)
    op.create_table(
        "automation_invalidations",
        sa.Column("key", sa.String(50), primary_key=True),
        sa.Column("source_id", sa.Uuid()),
        sa.Column("tag_id", sa.Uuid()),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("scan_revision", sa.BigInteger()),
        sa.Column("after_article_id", sa.Uuid()),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("progressed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "source_id IS NULL OR tag_id IS NULL", name="ck_automation_invalidation_owner"
        ),
    )
    op.execute("""
    CREATE FUNCTION devfeed_automation_enqueue(
      source uuid DEFAULT NULL, changed_tag uuid DEFAULT NULL
    ) RETURNS void
    LANGUAGE plpgsql AS $$
    BEGIN
      -- Source/tag policy changes keep their own foreground cursors even while
      -- a global maintenance pass is active; their wake must not wait for it.
      INSERT INTO automation_invalidations(key, source_id, tag_id)
        VALUES (CASE WHEN source IS NOT NULL THEN 'source:' || source::text
                     WHEN changed_tag IS NOT NULL THEN 'tag:' || changed_tag::text
                     ELSE 'all' END, source, changed_tag)
      ON CONFLICT (key) DO UPDATE SET revision = automation_invalidations.revision + 1;
    END $$
    """)
    op.execute("""
    CREATE FUNCTION devfeed_automation_wake(identifier uuid) RETURNS void
    LANGUAGE plpgsql AS $$
    BEGIN
      UPDATE articles
        SET automation_input_revision = automation_input_revision + 1,
            automation_next_check_at = LEAST(automation_next_check_at, statement_timestamp()),
            automation_wait_reason = NULL
        WHERE id = identifier AND review_status IN ('pending', 'approved')
          AND publication_status = 'unpublished';
    END $$
    """)
    op.execute("""
    CREATE FUNCTION devfeed_automation_article_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      NEW.automation_input_revision := OLD.automation_input_revision + 1;
      NEW.automation_next_check_at := LEAST(NEW.automation_next_check_at, statement_timestamp());
      NEW.automation_wait_reason := NULL;
      RETURN NEW;
    END $$
    """)
    op.execute(f"""CREATE TRIGGER automation_article_changed
      BEFORE UPDATE OF {ARTICLE_COLUMNS} ON articles FOR EACH ROW
      WHEN ({_different(ARTICLE_COLUMNS)})
      EXECUTE FUNCTION devfeed_automation_article_changed()""")
    op.execute("""
    CREATE FUNCTION devfeed_automation_dependency_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE previous_id uuid; current_id uuid;
    BEGIN
      IF TG_ARGV[0] = 'job' AND TG_OP = 'UPDATE' THEN
        -- In-progress specialist output, dispatch and lease bookkeeping are not
        -- completed input. Retry scheduling and terminal result edits still wake.
        IF NEW.status IN ('queued','running') AND OLD.status = NEW.status
           AND OLD.article_id IS NOT DISTINCT FROM NEW.article_id
           AND OLD.available_at IS NOT DISTINCT FROM NEW.available_at
           AND OLD.attempts IS NOT DISTINCT FROM NEW.attempts THEN
          RETURN NEW;
        END IF;
      END IF;
      IF TG_OP <> 'INSERT' THEN previous_id := OLD.article_id; END IF;
      IF TG_OP <> 'DELETE' THEN current_id := NEW.article_id; END IF;
      IF previous_id IS DISTINCT FROM current_id THEN
        PERFORM devfeed_automation_wake(previous_id);
      END IF;
      PERFORM devfeed_automation_wake(current_id);
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$
    """)
    for table, columns in CHILD_COLUMNS.items():
        mode = "job" if table.endswith("_jobs") else "entity"
        op.execute(f"""CREATE TRIGGER automation_dependency_created_deleted
          AFTER INSERT OR DELETE ON {table} FOR EACH ROW
          EXECUTE FUNCTION devfeed_automation_dependency_changed('{mode}')""")
        op.execute(f"""CREATE TRIGGER automation_dependency_changed
          AFTER UPDATE OF {columns} ON {table} FOR EACH ROW
          WHEN ({_different(columns)})
          EXECUTE FUNCTION devfeed_automation_dependency_changed('{mode}')""")
    op.execute("""CREATE TRIGGER automation_validation_changed
      AFTER UPDATE OF usage ON article_analysis_jobs FOR EACH ROW
      WHEN ((OLD.usage->'validation_version') IS DISTINCT FROM (NEW.usage->'validation_version'))
      EXECUTE FUNCTION devfeed_automation_dependency_changed('job')""")
    op.execute("""
    CREATE FUNCTION devfeed_automation_source_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM devfeed_automation_enqueue(NEW.id);
      RETURN NEW;
    END $$
    """)
    op.execute(f"""CREATE TRIGGER automation_source_changed
      AFTER UPDATE OF {SOURCE_COLUMNS} ON sources FOR EACH ROW
      WHEN ({_different(SOURCE_COLUMNS)})
      EXECUTE FUNCTION devfeed_automation_source_changed()""")
    op.execute("""
    CREATE FUNCTION devfeed_automation_global_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM devfeed_automation_enqueue(NULL);
      RETURN NULL;
    END $$
    """)
    # IDF retrieval and topic-wait matching depend on complete vocabularies.
    # Never reduce these changes to existing article/topic assignments.
    op.execute("""CREATE TRIGGER automation_catalog_changed
      AFTER INSERT OR UPDATE ON catalog_revisions FOR EACH ROW
      WHEN (NEW.name IN ('topics','tags'))
      EXECUTE FUNCTION devfeed_automation_global_changed()""")
    # Research descriptions, logos, billing usage and lease metadata cannot change
    # pending-topic term matching. Compare only its actual semantic dependencies.
    proposal_columns = (
        "id, slug, status, proposed->'name', proposed->'slug', "
        "proposed->'aliases', proposed->'keywords'"
    )
    op.execute(f"""
    CREATE FUNCTION devfeed_automation_proposals_changed() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (SELECT 1 FROM new_rows) THEN RETURN NULL; END IF;
      ELSIF TG_OP = 'DELETE' THEN
        IF NOT EXISTS (SELECT 1 FROM old_rows) THEN RETURN NULL; END IF;
      ELSIF TG_OP = 'UPDATE' THEN
        IF NOT EXISTS (
          (SELECT {proposal_columns} FROM new_rows EXCEPT SELECT {proposal_columns} FROM old_rows)
          UNION ALL
          (SELECT {proposal_columns} FROM old_rows EXCEPT SELECT {proposal_columns} FROM new_rows)
        ) THEN RETURN NULL; END IF;
      END IF;
      PERFORM devfeed_automation_enqueue(NULL);
      RETURN NULL;
    END $$
    """)
    for operation, referencing in (
        ("INSERT", "REFERENCING NEW TABLE AS new_rows"),
        ("DELETE", "REFERENCING OLD TABLE AS old_rows"),
        ("UPDATE", "REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows"),
    ):
        op.execute(f"""CREATE TRIGGER automation_proposals_{operation.lower()}
          AFTER {operation} ON topic_proposals {referencing}
          FOR EACH STATEMENT EXECUTE FUNCTION devfeed_automation_proposals_changed()""")
    op.execute("""
    CREATE FUNCTION devfeed_automation_tag_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM devfeed_automation_enqueue(NULL, NEW.id);
      RETURN NEW;
    END $$
    """)
    op.execute("""CREATE TRIGGER automation_tag_policy_changed
      AFTER UPDATE OF auto_link_topic,topic_id ON tags FOR EACH ROW
      WHEN (ROW(OLD.auto_link_topic,OLD.topic_id)
            IS DISTINCT FROM ROW(NEW.auto_link_topic,NEW.topic_id))
      EXECUTE FUNCTION devfeed_automation_tag_changed()""")
    for table in (*CHILD_COLUMNS, "articles", "sources", "catalog_revisions", "topic_proposals"):
        op.execute(f"""CREATE TRIGGER automation_dependency_truncated
          AFTER TRUNCATE ON {table} FOR EACH STATEMENT
          EXECUTE FUNCTION devfeed_automation_global_changed()""")
    # No population update/backfill runs in the catalog writer or migration.
    op.execute("SELECT devfeed_automation_enqueue(NULL)")


def downgrade():
    for table in (*CHILD_COLUMNS, "articles", "sources", "catalog_revisions", "topic_proposals"):
        op.execute(f"DROP TRIGGER automation_dependency_truncated ON {table}")
    op.execute("DROP TRIGGER automation_tag_policy_changed ON tags")
    op.execute("DROP FUNCTION devfeed_automation_tag_changed()")
    for operation in ("insert", "delete", "update"):
        op.execute(f"DROP TRIGGER automation_proposals_{operation} ON topic_proposals")
    op.execute("DROP FUNCTION devfeed_automation_proposals_changed()")
    op.execute("DROP TRIGGER automation_validation_changed ON article_analysis_jobs")
    op.execute("DROP TRIGGER automation_catalog_changed ON catalog_revisions")
    op.execute("DROP TRIGGER automation_source_changed ON sources")
    for table in CHILD_COLUMNS:
        op.execute(f"DROP TRIGGER automation_dependency_changed ON {table}")
        op.execute(f"DROP TRIGGER automation_dependency_created_deleted ON {table}")
    op.execute("DROP TRIGGER automation_article_changed ON articles")
    for name in ("global", "source", "dependency", "article"):
        op.execute(f"DROP FUNCTION devfeed_automation_{name}_changed()")
    op.execute("DROP FUNCTION devfeed_automation_wake(uuid)")
    op.execute("DROP FUNCTION devfeed_automation_enqueue(uuid,uuid)")
    op.drop_table("automation_invalidations")
    for column in (
        "automation_wait_reason",
        "automation_evaluated_revision",
        "automation_input_revision",
    ):
        op.drop_column("articles", column)
