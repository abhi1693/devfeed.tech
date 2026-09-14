"""Persist bounded topic decisions and their shared public evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "topic_decision_runs",
        sa.Column(
            "proposal_id",
            sa.Uuid(),
            sa.ForeignKey("topic_proposals.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(100)),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_topic_decision_runs_status", "topic_decision_runs", ["status"])


def downgrade():
    op.drop_table("topic_decision_runs")
