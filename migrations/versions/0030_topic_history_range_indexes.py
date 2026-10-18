"""Bound newer-topic-history probes without duplicating existing retention indexes."""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    for target in ("topic_id", "proposal_id"):
        op.create_index(
            f"ix_topic_analysis_{target}_newer",
            "topic_analysis_jobs",
            [target, "created_at", "id"],
            postgresql_where=sa.text(f"{target} IS NOT NULL"),
        )


def downgrade():
    for target in ("proposal_id", "topic_id"):
        op.drop_index(f"ix_topic_analysis_{target}_newer", table_name="topic_analysis_jobs")
