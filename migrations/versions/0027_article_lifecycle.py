"""Index current article processing and approved articles awaiting publication."""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

HISTORY = (
    ("article_enrichment_jobs", "ix_article_enrichment_history"),
    ("article_analysis_jobs", "ix_article_analysis_history"),
    ("article_image_jobs", "ix_article_image_history"),
)


def _ensure_index(name, table, columns, **options):
    # Like migration0006: each concurrent build commits independently. Repair an
    # interrupted invalid index before retrying; retain a completed valid build.
    valid = op.get_bind().scalar(
        sa.text("SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass(:name)"),
        {"name": name},
    )
    if valid is True:
        return
    if valid is False:
        op.drop_index(name, table_name=table, postgresql_concurrently=True)
    op.create_index(name, table, columns, postgresql_concurrently=True, **options)


def _replace_automation_index(*, approved):
    name = "ix_articles_automation_due"
    temporary = f"{name}_{'next' if approved else 'previous'}"
    predicate = (
        "review_status IN ('pending', 'approved')" if approved else "review_status = 'pending'"
    ) + " AND publication_status = 'unpublished'"
    _ensure_index(
        temporary,
        "articles",
        ["automation_next_check_at", "id"],
        postgresql_where=sa.text(predicate),
    )
    op.drop_index(name, table_name="articles", postgresql_concurrently=True, if_exists=True)
    op.execute(f'ALTER INDEX "{temporary}" RENAME TO "{name}"')
    op.drop_index(
        f"{name}_{'previous' if approved else 'next'}",
        table_name="articles",
        postgresql_concurrently=True,
        if_exists=True,
    )


def upgrade():
    # Preserve article ingestion and worker writes during history index builds.
    # A short lock timeout also bounds final metadata-only renames.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            for table, name in HISTORY:
                _ensure_index(
                    name, table, ["article_id", sa.text("created_at DESC"), sa.text("id DESC")]
                )
            _replace_automation_index(approved=True)
        finally:
            op.execute("RESET lock_timeout")


def downgrade():
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            _replace_automation_index(approved=False)
            for table, name in reversed(HISTORY):
                op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
        finally:
            op.execute("RESET lock_timeout")
