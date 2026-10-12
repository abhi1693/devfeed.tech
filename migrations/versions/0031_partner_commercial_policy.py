"""Inherit promotion entitlements and keep trusted approval scoped to each account."""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

ACCOUNT_CHECKS = {
    "ck_partner_approval_mode": "approval_mode IN ('qualified','trusted')",
    "ck_partner_outbound_destination": "outbound_destination IN ('product_url','listing_url')",
    "ck_partner_promotion_kind": "promotion_kind IS NULL OR promotion_kind IN ('product','ad')",
    "ck_partner_promotion_entitlement": (
        "promotion_kind IS DISTINCT FROM 'ad' OR tier IN ('gold','platinum','diamond')"
    ),
}
APPROVAL_CHECK = (
    "(commercial_approved_at IS NULL AND approval_source IS NULL) OR "
    "(commercial_approved_at IS NOT NULL AND approval_source IS NOT NULL "
    "AND approval_source = 'trusted')"
)


def upgrade():
    op.add_column("partner_accounts", sa.Column("promotion_kind", sa.String(20), nullable=True))
    op.add_column(
        "partner_accounts",
        sa.Column("approval_mode", sa.String(20), nullable=False, server_default="qualified"),
    )
    op.add_column(
        "partner_accounts",
        sa.Column(
            "outbound_destination", sa.String(20), nullable=False, server_default="product_url"
        ),
    )
    for name, condition in ACCOUNT_CHECKS.items():
        op.create_check_constraint(name, "partner_accounts", condition)
    op.add_column(
        "partner_campaigns",
        sa.Column("managed", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index(
        "uq_partner_managed_campaign",
        "partner_campaigns",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("managed"),
    )
    op.add_column(
        "partner_product_promotions",
        sa.Column("commercial_approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "partner_product_promotions", sa.Column("approval_source", sa.String(20), nullable=True)
    )
    op.create_check_constraint(
        "ck_partner_commercial_approval", "partner_product_promotions", APPROVAL_CHECK
    )


def downgrade():
    # Retain attribution and counters, but never turn a tier-managed agreement into
    # an independently live legacy campaign after its policy fields are removed.
    op.execute("UPDATE partner_campaigns SET status='paused' WHERE managed")
    op.drop_constraint(
        "ck_partner_commercial_approval", "partner_product_promotions", type_="check"
    )
    op.drop_column("partner_product_promotions", "approval_source")
    op.drop_column("partner_product_promotions", "commercial_approved_at")
    op.drop_index("uq_partner_managed_campaign", table_name="partner_campaigns")
    op.drop_column("partner_campaigns", "managed")
    for name in reversed(ACCOUNT_CHECKS):
        op.drop_constraint(name, "partner_accounts", type_="check")
    for name in ("outbound_destination", "approval_mode", "promotion_kind"):
        op.drop_column("partner_accounts", name)
