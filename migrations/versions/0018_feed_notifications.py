"""Durable followed-topic publication events with resumable recipient expansion."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

app_version = "0.0.1"
revision = "0018_feed_notifications"
down_revision = "0017_discovery_statistics"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feed_notification_events",
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("topic_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipient_cursor", sa.Uuid()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_feed_notification_pending",
        "feed_notification_events",
        ["created_at"],
        postgresql_where=sa.text("completed_at IS NULL"),
    )


def downgrade():
    op.drop_table("feed_notification_events")
