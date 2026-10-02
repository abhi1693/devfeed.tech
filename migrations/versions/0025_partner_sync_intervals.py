"""Configurable partner sync intervals with independent lifecycle fencing."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "partner_connections",
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=False, server_default="360"),
    )
    op.add_column(
        "partner_connections",
        sa.Column("sync_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    # Existing jobs carry the old connection revision; retain their lifecycle fence.
    op.execute("UPDATE partner_connections SET sync_revision = revision")
    op.create_check_constraint(
        "ck_partner_sync_interval",
        "partner_connections",
        "sync_interval_minutes BETWEEN 1 AND 10080",
    )


def downgrade():
    # Fence outstanding jobs before older workers return to the settings revision.
    op.execute("UPDATE partner_connections SET revision = GREATEST(revision, sync_revision) + 1")
    op.drop_constraint("ck_partner_sync_interval", "partner_connections", type_="check")
    op.drop_column("partner_connections", "sync_revision")
    op.drop_column("partner_connections", "sync_interval_minutes")
