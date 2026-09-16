"""Bound history payloads and index alphabetical reader discovery."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("article_analysis_jobs", "topic_analysis_jobs"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS inputs_pruned_at timestamptz")
    # Concurrent builds keep the live catalog and job queues writable. IF NOT
    # EXISTS permits resuming after an interrupted nontransactional index build;
    # rollout preflight must reject invalid indexes before retrying.
    with op.get_context().autocommit_block():
        invalid = (
            op.get_bind()
            .execute(
                sa.text("""
            SELECT indexrelid::regclass::text FROM pg_index
            WHERE NOT indisvalid AND indexrelid IN (
                to_regclass('ix_topics_active_name'),
                to_regclass('ix_article_analysis_jobs_retention'),
                to_regclass('ix_topic_analysis_jobs_retention')
            )
        """)
            )
            .scalars()
            .all()
        )
        if invalid:
            raise RuntimeError(f"Remove invalid indexes before retrying migration: {invalid}")
        op.execute("SET lock_timeout = '5s'")
        try:
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_topics_active_name "
                "ON topics (name, id) WHERE status = 'active'"
            )
            for table in ("article_analysis_jobs", "topic_analysis_jobs"):
                op.execute(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_{table}_retention "
                    f"ON {table} (finished_at, id) "
                    "WHERE status = 'succeeded' AND inputs_pruned_at IS NULL"
                )
        finally:
            op.execute("RESET lock_timeout")


def downgrade():
    for table in ("article_analysis_jobs", "topic_analysis_jobs"):
        op.drop_index(f"ix_{table}_retention", table_name=table)
        op.drop_column(table, "inputs_pruned_at")
    op.drop_index("ix_topics_active_name", table_name="topics")
