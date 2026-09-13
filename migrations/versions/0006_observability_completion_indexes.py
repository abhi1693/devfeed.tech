"""Index recent completion metadata without scanning completed payload history."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
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
    # CONCURRENTLY keeps production writes available. Each index is independently
    # committed; a retry repairs an invalid interrupted build before continuing.
    with op.get_context().autocommit_block():
        for table in TABLES:
            name = f"ix_{table}_finished_metrics"
            valid = op.get_bind().scalar(
                sa.text("SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass(:name)"),
                {"name": name},
            )
            if valid is True:
                continue
            if valid is False:
                op.drop_index(name, table_name=table, postgresql_concurrently=True)
            op.create_index(
                name,
                table,
                ["finished_at"],
                postgresql_include=["status", "attempts", "created_at"],
                postgresql_where=sa.text("finished_at IS NOT NULL"),
                postgresql_concurrently=True,
            )


def downgrade():
    with op.get_context().autocommit_block():
        for table in reversed(TABLES):
            op.drop_index(
                f"ix_{table}_finished_metrics",
                table_name=table,
                postgresql_concurrently=True,
                if_exists=True,
            )
