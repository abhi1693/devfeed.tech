"""Add durable, supervised topic research jobs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_topic_analysis"
down_revision = "0004_topics_ssot"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "topic_analysis_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.Uuid(),
            sa.ForeignKey("topic_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("requested_by", postgresql.JSONB(), nullable=False),
        sa.Column("model", sa.String(100)),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("outcome", sa.String(30)),
        sa.Column("error", sa.String(1000)),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        sa.CheckConstraint("attempts >= 0"),
    )
    op.create_index("ix_topic_analysis_jobs_proposal_id", "topic_analysis_jobs", ["proposal_id"])
    op.create_index(
        "uq_topic_analysis_active",
        "topic_analysis_jobs",
        ["proposal_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_index(
        "ix_topic_analysis_dispatch",
        "topic_analysis_jobs",
        ["available_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade():
    op.drop_table("topic_analysis_jobs")
