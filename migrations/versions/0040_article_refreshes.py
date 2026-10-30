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


def upgrade():
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
