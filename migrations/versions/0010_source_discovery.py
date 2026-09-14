"""Add source discovery and the unified pending-source lifecycle."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("sources", "feed_url", existing_type=sa.String(2048), nullable=True)
    op.create_check_constraint(
        "ck_sources_approved_feed",
        "sources",
        "approval_status <> 'approved' OR feed_url IS NOT NULL",
    )
    op.create_table(
        "discovery_seeds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("format", sa.String(length=20), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_table(
        "source_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("identity_url", sa.String(length=2048), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("approval_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("selected_feed_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("review", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','ready','unresolved','retry_wait','rejected','admitted','linked')"
        ),
        sa.CheckConstraint(
            "approval_status IN ('pending','approved','rejected')",
            name="source_candidate_approval_status",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_url"),
    )
    op.create_index(
        "ix_source_candidates_status_created",
        "source_candidates",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_source_candidates_approval_status", "source_candidates", ["approval_status"]
    )
    # Preserve an unresolved record when a source is removed outside the common
    # deletion service, which removes its discovery history explicitly.
    op.execute("""
        CREATE FUNCTION discovery_source_unlinked() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.source_id IS NOT NULL AND NEW.source_id IS NULL THEN
                NEW.status := 'unresolved';
                NEW.approval_status := 'pending';
                NEW.updated_at := now();
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER discovery_source_unlinked
        BEFORE UPDATE OF source_id ON source_candidates
        FOR EACH ROW EXECUTE FUNCTION discovery_source_unlinked();
    """)
    op.create_table(
        "candidate_discoveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("origin_key", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=2048), nullable=False),
        sa.Column("seed_id", sa.Uuid(), nullable=True),
        sa.Column("feed_hint", sa.String(length=2048), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["source_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["seed_id"],
            ["discovery_seeds.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", "origin_key"),
    )
    op.create_index(
        op.f("ix_candidate_discoveries_candidate_id"),
        "candidate_discoveries",
        ["candidate_id"],
        unique=False,
    )
    op.create_table(
        "candidate_feeds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["source_candidates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", "url"),
    )
    op.create_index(
        op.f("ix_candidate_feeds_candidate_id"), "candidate_feeds", ["candidate_id"], unique=False
    )
    op.create_table(
        "source_discovery_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("error", sa.String(length=100), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("stage IN ('discover','assess')"),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        sa.ForeignKeyConstraint(["candidate_id"], ["source_candidates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_source_discovery_due", "source_discovery_jobs", ["status", "available_at"], unique=False
    )
    op.create_index(
        "uq_source_discovery_active",
        "source_discovery_jobs",
        ["candidate_id", "stage"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_index(
        "uq_source_discovery_running",
        "source_discovery_jobs",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_table(
        "candidate_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["source_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["feed_id"],
            ["candidate_feeds.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_candidate_assessments_candidate_created",
        "candidate_assessments",
        ["candidate_id", "created_at"],
        unique=False,
    )
    # Existing pending sources also enter the common review queue. Discovery
    # tables are new, so no transitional candidate backfill is needed.
    op.execute("""
        INSERT INTO source_enrichment_jobs (id, source_id, status, attempts, available_at,
            created_at, changed_fields)
        SELECT gen_random_uuid(), s.id, 'queued', 0, now(), now(), ARRAY[]::varchar[] FROM sources s
        WHERE s.approval_status = 'pending' AND s.feed_url IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM source_enrichment_jobs j WHERE j.source_id = s.id
                          AND j.status IN ('queued', 'running'));
    """)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM sources WHERE feed_url IS NULL")):
        raise RuntimeError("Complete or delete sources awaiting feed discovery before downgrade")
    op.execute("DROP TRIGGER discovery_source_unlinked ON source_candidates")
    op.execute("DROP FUNCTION discovery_source_unlinked()")
    op.drop_table("candidate_assessments")
    op.drop_table("source_discovery_jobs")
    op.drop_table("candidate_feeds")
    op.drop_table("candidate_discoveries")
    op.drop_table("source_candidates")
    op.drop_table("discovery_seeds")
    op.drop_constraint("ck_sources_approved_feed", "sources", type_="check")
    op.alter_column("sources", "feed_url", existing_type=sa.String(2048), nullable=False)
