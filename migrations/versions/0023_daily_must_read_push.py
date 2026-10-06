"""Opt-in, session-bound browser delivery of one daily Must Read."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "web_push_subscriptions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("consent_id", sa.UUID(), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("endpoint_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("p256dh", sa.String(100), nullable=False),
        sa.Column("auth", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("authorization_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_push_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_web_push_subscriptions_user_id", "web_push_subscriptions", ["user_id"])
    op.create_index(
        "ix_web_push_subscriptions_session_hash", "web_push_subscriptions", ["session_hash"]
    )
    op.create_index(
        "ix_web_push_subscription_due",
        "web_push_subscriptions",
        ["next_push_at", "user_id"],
        postgresql_where=sa.text("enabled = true"),
    )
    op.create_table(
        "daily_must_read_pushes",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("selection_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("article_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "selection_date"),
    )
    op.create_index("ix_daily_must_read_pushes_user_id", "daily_must_read_pushes", ["user_id"])
    op.create_table(
        "web_push_deliveries",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "event_id",
            sa.UUID(),
            sa.ForeignKey("daily_must_read_pushes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subscription_id",
            sa.UUID(),
            sa.ForeignKey("web_push_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("consent_id", sa.UUID(), nullable=False),
        sa.Column("error", sa.String(200)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.UUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("event_id", "subscription_id"),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed')"),
        sa.CheckConstraint("attempts >= 0"),
    )
    op.create_index("ix_web_push_deliveries_event_id", "web_push_deliveries", ["event_id"])
    op.create_index(
        "ix_web_push_deliveries_subscription_id", "web_push_deliveries", ["subscription_id"]
    )
    op.create_index(
        "ix_web_push_delivery_dispatch",
        "web_push_deliveries",
        ["available_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_web_push_delivery_running_lease",
        "web_push_deliveries",
        ["lease_until"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade():
    op.drop_table("web_push_deliveries")
    op.drop_table("daily_must_read_pushes")
    op.drop_table("web_push_subscriptions")
