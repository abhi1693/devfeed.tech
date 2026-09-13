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


STATUS_INDEXES = (
    ("article_analysis_jobs", "status"),
    ("article_enrichment_jobs", "status"),
    ("notification_deliveries", "status"),
    ("articles", "publication_status"),
)


def definitions():
    for table in TABLES:
        yield (
            f"ix_{table}_finished_metrics",
            table,
            ["finished_at"],
            {
                "postgresql_include": ["status", "attempts", "created_at"],
                "postgresql_where": sa.text("finished_at IS NOT NULL"),
            },
        )
    for table, column in STATUS_INDEXES:
        yield f"ix_{table}_state_metrics", table, [column], {}


def upgrade():
    # CONCURRENTLY preserves writes. Each index commits independently; retries
    # repair an invalid interrupted build before continuing.
    with op.get_context().autocommit_block():
        for name, table, columns, options in definitions():
            valid = op.get_bind().scalar(
                sa.text("SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass(:name)"),
                {"name": name},
            )
            if valid is True:
                continue
            if valid is False:
                op.drop_index(name, table_name=table, postgresql_concurrently=True)
            op.create_index(name, table, columns, postgresql_concurrently=True, **options)


def downgrade():
    with op.get_context().autocommit_block():
        for name, table, _, _options in reversed(list(definitions())):
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
