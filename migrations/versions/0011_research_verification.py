"""DevFeed 0.0.1: durable citation recovery and independent relationship verification."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

app_version = "0.0.1"
revision = "0011_research_verification"
down_revision = "0010_tag_topic_discovery"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_verification_jobs",
        sa.Column(
            "id",
            sa.Uuid(),
            sa.ForeignKey("topic_analysis_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("relationships", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(30)),
        sa.Column("error", sa.String(1000)),
        sa.Column("usage", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        sa.CheckConstraint("attempts >= 0"),
    )
    op.create_index(
        "ix_research_verification_dispatch",
        "research_verification_jobs",
        ["available_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade():
    op.drop_table("research_verification_jobs")
