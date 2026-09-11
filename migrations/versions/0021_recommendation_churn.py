"""Bound recent-like reads and ignore unchanged publication assignments."""

import sqlalchemy as sa
from alembic import op

app_version = "0.0.1"
revision = "0021_recommendation_churn"
down_revision = "0020_user_recommendations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_article_likes_user_recent",
        "article_likes",
        ["user_id", sa.text("created_at DESC"), "article_id"],
    )
    op.execute("""
        DROP TRIGGER recommendation_articles ON articles;
        CREATE TRIGGER recommendation_articles
          AFTER UPDATE OF publication_status, review_status, feed_at ON articles
          FOR EACH ROW WHEN (
            (OLD.publication_status, OLD.review_status, OLD.feed_at)
            IS DISTINCT FROM (NEW.publication_status, NEW.review_status, NEW.feed_at)
          ) EXECUTE FUNCTION recommendation_catalog_change();
        DROP TRIGGER recommendation_sources ON sources;
        CREATE TRIGGER recommendation_sources AFTER UPDATE OF approval_status ON sources
          FOR EACH ROW WHEN (OLD.approval_status IS DISTINCT FROM NEW.approval_status)
          EXECUTE FUNCTION recommendation_catalog_change();
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER recommendation_articles ON articles;
        CREATE TRIGGER recommendation_articles
          AFTER UPDATE OF publication_status, review_status, feed_at ON articles
          FOR EACH ROW EXECUTE FUNCTION recommendation_catalog_change();
        DROP TRIGGER recommendation_sources ON sources;
        CREATE TRIGGER recommendation_sources AFTER UPDATE OF approval_status ON sources
          FOR EACH ROW EXECUTE FUNCTION recommendation_catalog_change();
    """)
    op.drop_index("ix_article_likes_user_recent", table_name="article_likes")
