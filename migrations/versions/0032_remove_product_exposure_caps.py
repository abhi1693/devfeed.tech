"""Remove per-reader product exposure settings without changing campaign budgets."""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_partner_frequency_cap", "partner_accounts", type_="check")
    op.drop_column("partner_accounts", "frequency_cap")


def downgrade():
    # Removed settings cannot be recovered. Older runtimes receive their original
    # default; campaign budgets, assets and historical measurements remain intact.
    op.add_column(
        "partner_accounts",
        sa.Column("frequency_cap", sa.Integer(), nullable=False, server_default="3"),
    )
    op.create_check_constraint(
        "ck_partner_frequency_cap", "partner_accounts", "frequency_cap BETWEEN 1 AND 20"
    )
