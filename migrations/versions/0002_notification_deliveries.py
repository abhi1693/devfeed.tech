"""Add audience-scoped transactional notification deliveries.

Revision ID: 0002_notifications
Revises: 0001_initial
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_notifications"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(255), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("audience", sa.String(16), nullable=False),
        sa.Column("subscriber_id", sa.String(128), nullable=True),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(200), nullable=True),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        sa.CheckConstraint("attempts >= 0"),
        sa.CheckConstraint("audience IN ('admin','user')"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key"),
    )
    op.create_index(
        "ix_notification_delivery_dispatch",
        "notification_deliveries",
        ["available_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade():
    op.drop_index("ix_notification_delivery_dispatch", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
