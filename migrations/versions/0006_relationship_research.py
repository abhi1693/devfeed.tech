"""Supervised AI relationship research for active topics."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_relationship_research"
down_revision = "0005_topic_analysis"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("topic_analysis_jobs", "proposal_id", nullable=True)
    op.add_column("topic_analysis_jobs", sa.Column("topic_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_topic_analysis_topic",
        "topic_analysis_jobs",
        "topics",
        ["topic_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_topic_analysis_target",
        "topic_analysis_jobs",
        "(proposal_id IS NULL) <> (topic_id IS NULL)",
    )
    op.create_index("ix_topic_analysis_jobs_topic_id", "topic_analysis_jobs", ["topic_id"])
    op.create_index(
        "uq_topic_relationship_analysis_active",
        "topic_analysis_jobs",
        ["topic_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_table(
        "topic_relation_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("topic_analysis_jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "topic_id", sa.Uuid(), sa.ForeignKey("topics.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "related_topic_id",
            sa.Uuid(),
            sa.ForeignKey("topics.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("relation", sa.String(50), nullable=False),
        sa.Column("explanation", sa.String(1000), nullable=False),
        sa.Column("evidence_url", sa.String(2048), nullable=False),
        sa.Column("evidence_title", sa.String(300), nullable=False),
        sa.Column("evidence_quote", sa.String(500), nullable=False),
        sa.Column("topic_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("related_topic_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.JSONB(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", postgresql.JSONB()),
        sa.Column("review_note", sa.String(1000)),
        sa.CheckConstraint("topic_id <> related_topic_id"),
        sa.CheckConstraint("status IN ('pending','approved','rejected')"),
        sa.CheckConstraint(
            "status = 'pending' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL)"
        ),
    )
    op.create_index(
        "uq_relation_proposal_pending",
        "topic_relation_proposals",
        ["topic_id", "related_topic_id", "relation"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    for column in ("job_id", "topic_id", "related_topic_id", "status"):
        op.create_index(
            f"ix_topic_relation_proposals_{column}", "topic_relation_proposals", [column]
        )


def downgrade():
    # Historical relationship runs have no metadata proposal to restore.
    raise RuntimeError(
        "Relationship research is forward-only; restore a pre-migration backup to roll back"
    )
