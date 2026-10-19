"""Invalidate visible-topic counts transactionally, including raw SQL and revocations."""

from migrations.search_visibility import (
    create_search_visibility_triggers,
    drop_search_visibility_triggers,
)

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

TABLES = {"article_topics": "article_id, topic_id, role"}


def upgrade():
    create_search_visibility_triggers(TABLES)


def downgrade():
    drop_search_visibility_triggers(TABLES)
