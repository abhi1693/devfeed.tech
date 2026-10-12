"""Durable, resumable automatic promotion enrollment."""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "partner_promotion_enrollments",
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("partner_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("product_cursor", sa.Uuid(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("sweep_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_partner_promotion_enrollment_due",
        "partner_promotion_enrollments",
        ["available_at", "updated_at", "account_id"],
    )


def downgrade():
    op.drop_table("partner_promotion_enrollments")
