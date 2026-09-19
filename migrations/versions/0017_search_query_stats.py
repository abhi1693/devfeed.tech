"""Store identity-free, moderated successful-query aggregates."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "search_query_stats",
        sa.Column("query_hash", sa.String(64), primary_key=True),
        sa.Column("query", sa.String(200), nullable=False, unique=True),
        sa.Column("successful_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(200)),
        sa.Column("review_note", sa.String(1000)),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected')", name="ck_search_query_stat_status"
        ),
        sa.CheckConstraint("successful_count >= 0", name="ck_search_query_stat_count"),
    )
    op.create_index(
        "ix_search_query_stats_discovery",
        "search_query_stats",
        ["status", "successful_count", "last_seen_at"],
    )


def downgrade():
    op.drop_index("ix_search_query_stats_discovery", table_name="search_query_stats")
    op.drop_table("search_query_stats")
