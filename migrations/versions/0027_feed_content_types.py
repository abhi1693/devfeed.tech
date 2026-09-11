"""Refresh prepared feeds when content preferences or article types change."""

from alembic import op

app_version = "0.0.1"
revision = "0027_feed_content_types"
down_revision = "0026_user_sources"
branch_labels = None
depends_on = None


def article_triggers(include_type):
    columns = "publication_status, review_status, feed_at"
    if include_type:
        columns += ", content_type"
    old = ", ".join(f"OLD.{column}" for column in columns.split(", "))
    new = old.replace("OLD.", "NEW.")
    for trigger, function in [
        ("recommendation_articles", "recommendation_catalog_change"),
        ("recommendation_source_articles", "recommendation_source_change"),
    ]:
        op.execute(f"DROP TRIGGER {trigger} ON articles")
        op.execute(f"""
            CREATE TRIGGER {trigger} AFTER UPDATE OF {columns} ON articles
              FOR EACH ROW WHEN (({old}) IS DISTINCT FROM ({new}))
              EXECUTE FUNCTION {function}();
        """)


def upgrade():
    op.execute("""
        CREATE TRIGGER recommendation_content_preferences
          AFTER UPDATE OF feed_settings ON user_accounts
          FOR EACH ROW WHEN (
            coalesce(OLD.feed_settings -> 'content_types',
              '["article","news","tutorial","release","comparison","opinion"]'::jsonb)
            IS DISTINCT FROM coalesce(NEW.feed_settings -> 'content_types',
              '["article","news","tutorial","release","comparison","opinion"]'::jsonb)
          ) EXECUTE FUNCTION recommendation_request_user();
    """)
    article_triggers(True)


def downgrade():
    op.execute("DROP TRIGGER recommendation_content_preferences ON user_accounts")
    article_triggers(False)
    op.execute("UPDATE user_accounts SET feed_settings = feed_settings - 'content_types'")
    op.execute("""
        UPDATE user_recommendation_states SET invalidated = true,
          next_refresh_at = now(), dispatched_at = NULL
    """)
