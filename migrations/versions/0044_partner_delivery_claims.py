"""Track operational receipt claims without analytical counters.

Revision ID: 0044
Revises: 0043
"""

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "partner_placement_deliveries",
        sa.Column("impression_recorded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "partner_placement_deliveries",
        sa.Column("click_recorded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("partner_placement_deliveries", "click_recorded_at")
    op.drop_column("partner_placement_deliveries", "impression_recorded_at")
