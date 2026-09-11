"""Rename reader identity and preference storage to user terminology, preserving data."""

from alembic import op

app_version = "0.0.1"
revision = "0016_user_accounts"
down_revision = "0015_article_engagement"
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table("reader_accounts", "user_accounts")
    op.rename_table("reader_topics", "user_topics")
    op.execute(
        "ALTER TABLE user_accounts RENAME CONSTRAINT reader_accounts_pkey TO user_accounts_pkey"
    )
    op.execute("ALTER TABLE user_topics RENAME CONSTRAINT reader_topics_pkey TO user_topics_pkey")
    op.execute(
        "ALTER TABLE user_topics RENAME CONSTRAINT "
        "reader_topics_reader_id_fkey TO user_topics_user_id_fkey"
    )
    op.execute(
        "ALTER TABLE user_topics RENAME CONSTRAINT "
        "reader_topics_topic_id_fkey TO user_topics_topic_id_fkey"
    )
    op.execute(
        "ALTER TABLE article_likes RENAME CONSTRAINT "
        "article_likes_reader_id_fkey TO article_likes_user_id_fkey"
    )
    op.alter_column("user_topics", "reader_id", new_column_name="user_id")
    op.alter_column("article_likes", "reader_id", new_column_name="user_id")
    op.execute("ALTER TABLE user_accounts RENAME CONSTRAINT uq_reader_identity TO uq_user_identity")
    op.execute("ALTER INDEX ix_reader_topics_topic_id RENAME TO ix_user_topics_topic_id")
    op.execute("ALTER INDEX ix_article_likes_reader_id RENAME TO ix_article_likes_user_id")


def downgrade():
    op.execute(
        "ALTER TABLE article_likes RENAME CONSTRAINT "
        "article_likes_user_id_fkey TO article_likes_reader_id_fkey"
    )
    op.execute(
        "ALTER TABLE user_topics RENAME CONSTRAINT "
        "user_topics_topic_id_fkey TO reader_topics_topic_id_fkey"
    )
    op.execute(
        "ALTER TABLE user_topics RENAME CONSTRAINT "
        "user_topics_user_id_fkey TO reader_topics_reader_id_fkey"
    )
    op.execute("ALTER TABLE user_topics RENAME CONSTRAINT user_topics_pkey TO reader_topics_pkey")
    op.execute(
        "ALTER TABLE user_accounts RENAME CONSTRAINT user_accounts_pkey TO reader_accounts_pkey"
    )
    op.execute("ALTER INDEX ix_article_likes_user_id RENAME TO ix_article_likes_reader_id")
    op.execute("ALTER INDEX ix_user_topics_topic_id RENAME TO ix_reader_topics_topic_id")
    op.execute("ALTER TABLE user_accounts RENAME CONSTRAINT uq_user_identity TO uq_reader_identity")
    op.alter_column("article_likes", "user_id", new_column_name="reader_id")
    op.alter_column("user_topics", "user_id", new_column_name="reader_id")
    op.rename_table("user_topics", "reader_topics")
    op.rename_table("user_accounts", "reader_accounts")
