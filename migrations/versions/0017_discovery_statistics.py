"""Capture skewed topic/source membership for selective feed planning."""

from alembic import op

app_version = "0.0.1"
revision = "0017_discovery_statistics"
down_revision = "0016_user_accounts"
branch_labels = None
depends_on = None


def upgrade():
    # The default sample omitted rare topics/sources from most-common values.
    # PostgreSQL then assumed hundreds of matches and scanned the ordered feed.
    op.execute("ALTER TABLE article_topics ALTER COLUMN topic_id SET STATISTICS 1000")
    op.execute("ALTER TABLE article_origins ALTER COLUMN source_id SET STATISTICS 1000")
    op.execute("ANALYZE article_topics (topic_id)")
    op.execute("ANALYZE article_origins (source_id)")


def downgrade():
    op.execute("ALTER TABLE article_topics ALTER COLUMN topic_id SET STATISTICS -1")
    op.execute("ALTER TABLE article_origins ALTER COLUMN source_id SET STATISTICS -1")
    op.execute("ANALYZE article_topics (topic_id)")
    op.execute("ANALYZE article_origins (source_id)")
