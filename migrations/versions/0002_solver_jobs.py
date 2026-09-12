"""Persist the dedicated solver execution lane across retries and recovery."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("article_enrichment_jobs", "source_enrichment_jobs", "article_image_jobs"):
        op.add_column(
            table,
            sa.Column("requires_solver", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade():
    for table in ("article_enrichment_jobs", "source_enrichment_jobs", "article_image_jobs"):
        op.drop_column(table, "requires_solver")
