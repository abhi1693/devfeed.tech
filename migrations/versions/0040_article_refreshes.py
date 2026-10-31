"""Keep replacement article work private until a guarded publication swap.

Revision ID: 0040
Revises: 0039
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

JOB_TABLES = ("article_enrichment_jobs", "article_analysis_jobs", "article_image_jobs")


SCHEMA_CHECKPOINT = "Private article maintenance ownership (0040)."


def _create_schema():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "article_refreshes",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("base_hash", sa.String(64), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("image_url", sa.String(2048), nullable=True),
        sa.Column("managed_image", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("requested_by", postgresql.JSONB(), nullable=False),
        sa.Column("approved_version", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_by", postgresql.JSONB(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_check_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("payload_pruned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "enrichment_job_id",
            sa.Uuid(),
            sa.ForeignKey("article_enrichment_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "analysis_job_id",
            sa.Uuid(),
            sa.ForeignKey("article_analysis_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "discovery_job_id",
            sa.Uuid(),
            sa.ForeignKey("article_image_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "storage_job_id",
            sa.Uuid(),
            sa.ForeignKey("article_image_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "target IN ('enrichment','analysis','images')", name="ck_refresh_target"
        ),
        sa.CheckConstraint(
            "status IN ('active','review','promoted','superseded','cancelled')",
            name="ck_refresh_status",
        ),
        sa.CheckConstraint("base_revision >= 0 AND version > 0", name="ck_refresh_versions"),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 1048576",
            name="ck_refresh_payload",
        ),
        comment=SCHEMA_CHECKPOINT,
    )
    op.create_index("ix_article_refreshes_article_id", "article_refreshes", ["article_id"])
    op.create_index(
        "uq_article_refresh_active",
        "article_refreshes",
        ["article_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active','review')"),
    )
    op.create_index(
        "ix_article_refresh_due",
        "article_refreshes",
        ["next_check_at", "id"],
        postgresql_where=sa.text("status IN ('active','review')"),
    )
    op.create_index(
        "ix_article_refresh_retention",
        "article_refreshes",
        ["completed_at", "id"],
        postgresql_where=sa.text(
            "payload_pruned_at IS NULL AND completed_at IS NOT NULL "
            "AND status IN ('promoted','superseded','cancelled')"
        ),
    )
    for table in JOB_TABLES:
        op.add_column(table, sa.Column("refresh_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_refresh_id",
            table,
            "article_refreshes",
            ["refresh_id"],
            ["id"],
            deferrable=True,
            initially="DEFERRED",
            postgresql_not_valid=True,
        )


def _repair_interrupted_index(name, table):
    if op.get_context().as_sql:
        return
    current = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT parent.relname AS table_name,idx.relkind AS relation_kind,"
                "i.indisvalid,i.indisunique,method.amname AS method,i.indnatts AS attributes,"
                "i.indexprs IS NOT NULL AS expressions,"
                "pg_get_expr(i.indpred,i.indrelid) AS predicate,"
                "ARRAY(SELECT a.attname::text FROM unnest(i.indkey) "
                "WITH ORDINALITY AS key(attnum,position) JOIN pg_attribute a "
                "ON a.attrelid=parent.oid AND a.attnum=key.attnum ORDER BY key.position) "
                "AS columns FROM pg_class idx JOIN pg_namespace ns ON ns.oid=idx.relnamespace "
                "LEFT JOIN pg_am method ON method.oid=idx.relam "
                "LEFT JOIN pg_index i ON i.indexrelid=idx.oid "
                "LEFT JOIN pg_class parent ON parent.oid=i.indrelid "
                "WHERE ns.nspname=current_schema() AND idx.relname=:name"
            ),
            {"name": name},
        )
        .mappings()
        .one_or_none()
    )
    if current is None:
        return
    if (
        current["relation_kind"] != "i"
        or current["table_name"] != table
        or current["columns"] != ["refresh_id"]
        or current["indisunique"]
        or current["predicate"] != "(refresh_id IS NOT NULL)"
        or current["method"] != "btree"
        or current["attributes"] != 1
        or current["expressions"]
    ):
        raise RuntimeError(f"Unexpected definition for maintenance index {name}")
    if not current["indisvalid"]:
        op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)


def upgrade():
    # The first transaction commits all additive DDL before concurrent indexes.
    # A retry remains at 0039; only this migration's committed checkpoint may
    # resume the index phase. Never silently adopt an unrelated existing table.
    if op.get_context().as_sql:
        _create_schema()
    else:
        inspector = sa.inspect(op.get_bind())
        if not inspector.has_table("article_refreshes"):
            _create_schema()
        elif inspector.get_table_comment("article_refreshes")["text"] != SCHEMA_CHECKPOINT:
            raise RuntimeError("Unexpected existing article_refreshes relation")
    with op.get_context().autocommit_block():
        for table in JOB_TABLES:
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT fk_{table}_refresh_id")
            name = f"ix_{table}_refresh_id"
            _repair_interrupted_index(name, table)
            op.create_index(
                name,
                table,
                ["refresh_id"],
                postgresql_where=sa.text("refresh_id IS NOT NULL"),
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    # Prevent a new owner appearing between the emptiness check and table removal.
    op.execute("LOCK TABLE article_refreshes IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM article_refreshes) THEN
            RAISE EXCEPTION 'Cannot discard article maintenance history; roll forward';
          END IF;
        END $$
    """)
    for table in reversed(JOB_TABLES):
        op.drop_column(table, "refresh_id")
    op.drop_table("article_refreshes")
