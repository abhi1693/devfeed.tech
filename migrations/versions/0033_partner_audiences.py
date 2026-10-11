"""Retain optional audience targeting separately from product inventory filters."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "partner_accounts",
        sa.Column("audience", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "partner_campaigns",
        sa.Column("audience", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_partner_account_audience", "partner_accounts", "jsonb_typeof(audience) = 'object'"
    )
    op.create_check_constraint(
        "ck_partner_campaign_audience",
        "partner_campaigns",
        "audience IS NULL OR jsonb_typeof(audience) = 'object'",
    )


def downgrade():
    # Older runtimes cannot enforce audience restrictions. Pause targeted inventory
    # rather than silently broadening it when these settings disappear.
    populated = (
        "audience - 'experience_levels' - 'language_topic_ids' - 'tool_topic_ids' <> '{}'::jsonb"
        " OR COALESCE(audience->'experience_levels', '[]'::jsonb) <> '[]'::jsonb"
        " OR COALESCE(audience->'language_topic_ids', '[]'::jsonb) <> '[]'::jsonb"
        " OR COALESCE(audience->'tool_topic_ids', '[]'::jsonb) <> '[]'::jsonb"
    )
    op.execute(f"UPDATE partner_accounts SET status='paused' WHERE ({populated})")
    op.execute(
        f"UPDATE partner_campaigns SET status='paused' WHERE audience IS NOT NULL AND ({populated})"
    )
    op.drop_constraint("ck_partner_campaign_audience", "partner_campaigns", type_="check")
    op.drop_constraint("ck_partner_account_audience", "partner_accounts", type_="check")
    op.drop_column("partner_campaigns", "audience")
    op.drop_column("partner_accounts", "audience")
