"""Build bounded automation fanout indexes independently of transactional capture DDL."""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

INDEXES = {
    "ix_articles_automation_lane_due": (
        "ON articles ((CASE WHEN automation_wait_reason IN "
        "('maintenance','dependencies','source_admission','content_date_deferred') "
        "THEN 1 ELSE 0 END), automation_next_check_at, id) "
        "WHERE review_status IN ('pending','approved') AND publication_status='unpublished'"
    ),
    "ix_articles_automation_identity": (
        "ON articles (id) WHERE review_status IN ('pending','approved') "
        "AND publication_status='unpublished'"
    ),
    "ix_article_origins_source_article": "ON article_origins (source_id, article_id)",
}


def upgrade():
    # An interrupted concurrent build leaves an invalid catalog entry. Recover
    # only our own invalid indexes; completed valid builds survive retry.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            for name, definition in INDEXES.items():
                invalid = op.get_bind().scalar(
                    sa.text(
                        "SELECT NOT indisvalid FROM pg_index WHERE indexrelid=to_regclass(:name)"
                    ),
                    {"name": name},
                )
                if invalid:
                    op.execute(f"DROP INDEX CONCURRENTLY {name}")
                op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} {definition}")
        finally:
            op.execute("RESET lock_timeout")


def downgrade():
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            for name in reversed(INDEXES):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
        finally:
            op.execute("RESET lock_timeout")
