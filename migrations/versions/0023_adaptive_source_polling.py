"""Add opt-in source cadence state without changing existing configured intervals."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sources", sa.Column("polling_mode", sa.String(20), nullable=False, server_default="fixed")
    )
    op.add_column(
        "sources", sa.Column("polling_state", JSONB(), nullable=False, server_default="{}")
    )
    op.add_column("sources", sa.Column("scheduled_polling_mode", sa.String(20), nullable=True))
    op.add_column("sources", sa.Column("scheduled_interval_seconds", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_sources_polling_mode", "sources", "polling_mode IN ('fixed','adaptive')"
    )
    op.create_index(
        "ix_sources_polling_reconcile",
        "sources",
        ["scheduled_polling_mode", "polling_mode", "id"],
        postgresql_where=sa.text(
            "enabled AND approval_status = 'approved' AND "
            "(polling_mode = 'adaptive' OR scheduled_polling_mode = 'adaptive')"
        ),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("automatic", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("new_source_entries", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_index("ix_sources_polling_reconcile", table_name="sources")
    for column in ("new_source_entries", "automatic"):
        op.drop_column("ingestion_jobs", column)
    op.drop_constraint("ck_sources_polling_mode", "sources", type_="check")
    for column in (
        "scheduled_interval_seconds",
        "scheduled_polling_mode",
        "polling_state",
        "polling_mode",
    ):
        op.drop_column("sources", column)
