"""DevFeed 0.0.1: resumable relationship coverage for a growing topic catalog."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

app_version = "0.0.1"
revision = "0009_relationship_coverage"
down_revision = "0008_autonomous_pipeline"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.schema.CreateSequence(sa.Sequence("topic_relationship_generation")))
    op.create_table(
        "topic_relationship_scans",
        sa.Column(
            "topic_id", sa.Uuid(), sa.ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("nextval('topic_relationship_generation')"),
        ),
        sa.Column("topic_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("after_topic_id", sa.Uuid()),
        sa.Column(
            "job_id", sa.Uuid(), sa.ForeignKey("topic_analysis_jobs.id", ondelete="SET NULL")
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(1000)),
        sa.CheckConstraint("failures >= 0"),
    )
    for field in ("generation", "job_id"):
        op.create_index(f"ix_topic_relationship_scans_{field}", "topic_relationship_scans", [field])
    op.create_index(
        "ix_topic_relationship_scans_pending",
        "topic_relationship_scans",
        ["next_run_at"],
        postgresql_where=sa.text("finished_at IS NULL"),
    )


def downgrade():
    op.drop_table("topic_relationship_scans")
    op.execute(sa.schema.DropSequence(sa.Sequence("topic_relationship_generation")))
