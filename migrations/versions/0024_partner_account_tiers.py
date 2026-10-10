"""Restrict partnership accounts to the five predefined tiers."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    # Draft accounts used arbitrary labels. Preserve recognized tiers and assign
    # Bronze to unrecognized labels; benefits and memberships remain unchanged.
    op.execute("""
        UPDATE partner_accounts SET tier = CASE
            WHEN lower(trim(tier)) IN ('bronze','silver','gold','platinum','diamond')
            THEN lower(trim(tier)) ELSE 'bronze' END
    """)
    op.drop_column("partner_accounts", "benefits")
    op.create_check_constraint(
        "ck_partner_account_tier",
        "partner_accounts",
        "tier IN ('bronze','silver','gold','platinum','diamond')",
    )


def downgrade():
    op.add_column(
        "partner_accounts", sa.Column("benefits", JSONB(), nullable=False, server_default="[]")
    )
    op.drop_constraint("ck_partner_account_tier", "partner_accounts", type_="check")
