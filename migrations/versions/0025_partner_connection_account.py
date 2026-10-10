"""Associate each partner connection with at most one account."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("partner_connections", sa.Column("account_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_partner_connection_account",
        "partner_connections",
        "partner_accounts",
        ["account_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_partner_connections_account_id", "partner_connections", ["account_id"])


def downgrade():
    op.drop_index("ix_partner_connections_account_id", table_name="partner_connections")
    op.drop_constraint("fk_partner_connection_account", "partner_connections", type_="foreignkey")
    op.drop_column("partner_connections", "account_id")
