"""Verified reader signup cohorts and original-open activation diagnostics."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_accounts", sa.Column("reader_signup_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_user_accounts_reader_signup_at",
        "user_accounts",
        ["reader_signup_at"],
        postgresql_where=sa.text("reader_signup_at IS NOT NULL"),
    )
    op.create_table(
        "reader_signups",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("signup_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acquisition_at", sa.DateTime(timezone=True)),
        sa.Column("acquisition_status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(64)),
        sa.Column("medium", sa.String(64)),
        sa.Column("campaign", sa.String(64)),
        sa.Column("landing_route", sa.String(20), nullable=False),
        sa.Column("client_platform", sa.String(20), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("activation_article_id", sa.Uuid()),
        sa.CheckConstraint(
            "acquisition_status IN ('attributed','direct','unknown','unavailable')",
            name="ck_reader_signup_acquisition",
        ),
        sa.CheckConstraint(
            "(activated_at IS NULL) = (activation_article_id IS NULL)",
            name="ck_reader_signup_activation_pair",
        ),
        sa.CheckConstraint(
            "activated_at IS NULL OR (activated_at >= signup_at "
            "AND activated_at < signup_at + interval '7 days')",
            name="ck_reader_signup_activation_window",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_reader_signups_signup_at", "reader_signups", ["signup_at"])
    op.create_table(
        "reader_milestone_deliveries",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_name", sa.String(30), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "event_name IN ('sign_up','reader_activation')", name="ck_reader_milestone_event"
        ),
        sa.CheckConstraint(
            "status IN ('unavailable','pending','attempted','http_accepted','failed','unknown')",
            name="ck_reader_milestone_status",
        ),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 1", name="ck_reader_milestone_attempts"),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "event_name"),
        sa.UniqueConstraint("id"),
    )
    op.create_index(
        "ix_reader_milestone_pending", "reader_milestone_deliveries", ["status", "occurred_at"]
    )


def downgrade():
    op.drop_table("reader_milestone_deliveries")
    op.drop_table("reader_signups")
    op.drop_index("ix_user_accounts_reader_signup_at", table_name="user_accounts")
    op.drop_column("user_accounts", "reader_signup_at")
