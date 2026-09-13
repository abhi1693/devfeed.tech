"""Bound recovery scans to running jobs instead of completed payload history."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLES = (
    "ingestion_jobs",
    "article_image_jobs",
    "source_enrichment_jobs",
    "article_enrichment_jobs",
    "article_analysis_jobs",
    "topic_analysis_jobs",
    "research_verification_jobs",
    "notification_deliveries",
)


def upgrade():
    for table in TABLES:
        op.create_index(
            f"ix_{table}_running_lease",
            table,
            ["lease_until", "id"],
            postgresql_where=sa.text("status = 'running'"),
        )


def downgrade():
    for table in reversed(TABLES):
        op.drop_index(f"ix_{table}_running_lease", table_name=table)
