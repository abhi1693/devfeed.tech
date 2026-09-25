"""Add user profile identity, showcase, and reading streak data."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_accounts", sa.Column("username", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_user_accounts_username", "user_accounts", ["username"])
    op.create_check_constraint(
        "ck_user_username", "user_accounts", "username ~ '^[a-z0-9][a-z0-9_-]{1,28}[a-z0-9]$'"
    )
    op.add_column("user_accounts", sa.Column("about", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_user_username_reserved",
        "user_accounts",
        "username NOT IN ('admin','api','auth','devfeed','help','login','logout','me','new',"
        "'profiles','settings','signup','support','system','www')",
    )

    op.create_table(
        "user_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("label", sa.String(80), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "url", name="uq_user_link_url"),
        sa.UniqueConstraint("user_id", "position", name="uq_user_link_position"),
    )
    op.create_index("ix_user_links_user_id", "user_links", ["user_id"])

    op.create_table(
        "user_stack_associations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=False),
        sa.Column("section", sa.String(length=20), nullable=False),
        sa.Column("since_year", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "section IN ('primary', 'hobby', 'learning', 'past')",
            name="ck_user_stack_section",
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "position", name="uq_user_stack_position"),
        sa.UniqueConstraint("user_id", "topic_id", name="uq_user_stack_topic"),
    )
    op.create_index("ix_user_stack_associations_user_id", "user_stack_associations", ["user_id"])
    op.create_index("ix_user_stack_associations_topic_id", "user_stack_associations", ["topic_id"])

    op.create_table(
        "user_reading_streaks",
        sa.CheckConstraint(
            "current_days >= 0 AND longest_days >= current_days AND total_days >= longest_days",
            name="ck_user_streak_counts",
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("current_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("longest_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_read_date", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "user_reading_days",
        sa.CheckConstraint("article_count > 0", name="ck_reading_day_count"),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("read_date", sa.Date(), nullable=False),
        sa.Column("article_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "last_read_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "read_date"),
    )

    op.create_table(
        "user_reading_events",
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("read_date", sa.Date(), primary_key=True),
        sa.Column("article_id", sa.UUID(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(30), nullable=False, server_default="article_open"),
        sa.Column("rule_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_table("user_reading_events")
    op.drop_table("user_reading_days")
    op.drop_table("user_reading_streaks")
    op.drop_index("ix_user_stack_associations_topic_id", table_name="user_stack_associations")
    op.drop_index("ix_user_stack_associations_user_id", table_name="user_stack_associations")
    op.drop_table("user_stack_associations")
    op.drop_index("ix_user_links_user_id", table_name="user_links")
    op.drop_table("user_links")
    op.drop_column("user_accounts", "about")
    op.drop_constraint("uq_user_accounts_username", "user_accounts", type_="unique")
    op.drop_constraint("ck_user_username", "user_accounts", type_="check")
    op.drop_constraint("ck_user_username_reserved", "user_accounts", type_="check")
    op.drop_column("user_accounts", "username")
