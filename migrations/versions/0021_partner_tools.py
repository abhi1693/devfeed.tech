"""Private partner inventory and shadow evaluation outbox."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "partner_products",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(200), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("product_url", sa.String(2048), nullable=False),
        sa.Column("listing_url", sa.String(2048), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("pricing", sa.String(20), nullable=False),
        sa.Column("technologies", JSONB(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("attribution", sa.String(300), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviews", JSONB(), nullable=False),
        sa.UniqueConstraint("provider", "external_id", name="uq_partner_identity"),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','paused','withdrawn')",
            name="ck_partner_status",
        ),
    )
    op.create_table(
        "partner_evaluations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Uuid(),
            sa.ForeignKey("partner_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column("reviews", JSONB(), nullable=False),
        sa.Column("requested_by", JSONB(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "uq_partner_evaluation_active",
        "partner_evaluations",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_index("ix_partner_evaluation_created", "partner_evaluations", ["created_at"])
    op.create_index("ix_partner_evaluations_product_id", "partner_evaluations", ["product_id"])


def downgrade():
    op.drop_table("partner_evaluations")
    op.drop_table("partner_products")
