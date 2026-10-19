"""Invalidate visible-tag counts transactionally, including raw SQL and revocations."""

from alembic import op

from migrations.search_visibility import (
    create_search_visibility_triggers,
    drop_search_visibility_triggers,
)

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

TABLES = {
    "articles": "id, publication_status, review_status",
    "sources": "id, approval_status",
    "article_origins": "id, article_id, source_id",
    "article_tags": "article_id, tag_id",
}


def upgrade():
    op.execute("INSERT INTO catalog_revisions(name) VALUES ('search_tag_visibility')")
    create_search_visibility_triggers(TABLES)


def downgrade():
    drop_search_visibility_triggers(TABLES)
    op.execute("DELETE FROM catalog_revisions WHERE name='search_tag_visibility'")
