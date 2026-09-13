"""Persist private read-later lists with indexed keyset pagination."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "article_bookmarks",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_article_bookmarks_article_id", "article_bookmarks", ["article_id"])
    op.create_index(
        "ix_article_bookmarks_user_recent",
        "article_bookmarks",
        ["user_id", sa.text("created_at DESC"), sa.text("article_id DESC")],
    )


def downgrade():
    op.drop_table("article_bookmarks")
