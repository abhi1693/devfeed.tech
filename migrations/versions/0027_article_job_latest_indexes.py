"""Bound latest article-job lookups without sorting each article's full history."""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("article_analysis", "article_enrichment"):
        op.create_index(
            f"ix_{name}_latest",
            f"{name}_jobs",
            ["article_id", sa.text("created_at DESC"), sa.text("id DESC")],
        )


def downgrade():
    for name in ("article_enrichment", "article_analysis"):
        op.drop_index(f"ix_{name}_latest", table_name=f"{name}_jobs")
