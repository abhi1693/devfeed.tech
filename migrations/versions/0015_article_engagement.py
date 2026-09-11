"""Article hearts, durable open counters and recent deduplicated activity."""

import sqlalchemy as sa
from alembic import op

app_version = "0.0.1"
revision = "0015_article_engagement"
down_revision = "0014_reader_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "article_engagement",
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("opens", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "article_likes",
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "reader_id",
            sa.Uuid(),
            sa.ForeignKey("reader_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_article_likes_reader_id", "article_likes", ["reader_id"])
    op.create_index("ix_article_likes_created_at", "article_likes", ["created_at"])
    op.create_table(
        "article_opens",
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("viewer_key", sa.String(64), primary_key=True),
        sa.Column("opened_hour", sa.DateTime(timezone=True), primary_key=True),
    )
    op.create_index("ix_article_opens_opened_hour", "article_opens", ["opened_hour"])


def downgrade():
    op.drop_table("article_opens")
    op.drop_table("article_likes")
    op.drop_table("article_engagement")
