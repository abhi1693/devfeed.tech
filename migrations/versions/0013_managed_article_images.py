"""Persist managed thumbnails and reuse the image job pipeline for storage."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "articles",
        sa.Column("managed_image", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "article_image_jobs",
        sa.Column("operation", sa.String(20), nullable=False, server_default="discover"),
    )
    op.add_column(
        "article_image_jobs",
        sa.Column("storage", postgresql.JSONB(), nullable=False, server_default="{}"),
    )


def downgrade():
    op.drop_column("article_image_jobs", "storage")
    op.drop_column("article_image_jobs", "operation")
    op.drop_column("articles", "managed_image")
