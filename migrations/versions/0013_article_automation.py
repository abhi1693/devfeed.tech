"""DevFeed 0.0.1: durable and bounded article automation scheduling."""

import sqlalchemy as sa
from alembic import op

app_version = "0.0.1"
revision = "0013_article_automation"
down_revision = "0012_topics_landing_page"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("articles", sa.Column("automation_started_at", sa.DateTime(timezone=True)))
    op.add_column(
        "articles",
        sa.Column(
            "automation_next_check_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_articles_automation_due",
        "articles",
        ["automation_next_check_at", "id"],
        postgresql_where=sa.text(
            "review_status = 'pending' AND publication_status = 'unpublished'"
        ),
    )


def downgrade():
    op.drop_index("ix_articles_automation_due", table_name="articles")
    op.drop_column("articles", "automation_next_check_at")
    op.drop_column("articles", "automation_started_at")
