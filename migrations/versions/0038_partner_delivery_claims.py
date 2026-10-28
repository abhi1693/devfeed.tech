"""Track operational receipt claims without analytical counters.

Revision ID: 0038
Revises: 0037
"""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
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
