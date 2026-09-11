"""Retain aggregate daily overview history and index windowed analytics."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

app_version = "0.0.1"
revision = "0028_overview_daily"
down_revision = "0027_feed_content_types"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "overview_daily",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for table, column in [
        ("articles", "published_to_feed_at"),
        ("articles", "discovered_at"),
        ("user_accounts", "created_at"),
    ]:
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table, column in [
        ("articles", "published_to_feed_at"),
        ("articles", "discovered_at"),
        ("user_accounts", "created_at"),
    ]:
        op.drop_index(f"ix_{table}_{column}", table_name=table)
    op.drop_table("overview_daily")
