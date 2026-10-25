"""Separate recent report viewers from durable refresh ownership."""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "report_demand",
        sa.Column("group", sa.String(50), primary_key=True),
        sa.Column("days", sa.Integer(), primary_key=True),
        sa.Column("last_requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("days BETWEEN 1 AND 90", name="ck_report_demand_days"),
    )
    op.create_index("ix_report_demand_active", "report_demand", ["active_until"])


def downgrade():
    op.drop_index("ix_report_demand_active", table_name="report_demand")
    op.drop_table("report_demand")
