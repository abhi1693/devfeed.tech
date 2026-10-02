"""API-managed partner connections and automated qualification jobs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "partner_connections",
        sa.Column("provider", sa.String(200), primary_key=True),
        sa.Column(
            "partnership_type", sa.String(40), nullable=False, server_default="launch_platform"
        ),
        sa.CheckConstraint(
            "partnership_type = 'launch_platform'", name="ck_partner_connection_type"
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("updated_by", JSONB(), nullable=False),
    )
    op.add_column(
        "partner_products",
        sa.Column("connection_id", sa.String(200), sa.ForeignKey("partner_connections.provider")),
    )
    op.add_column("partner_products", sa.Column("seen_generation", sa.Uuid()))
    op.add_column(
        "partner_products", sa.Column("assessment", JSONB(), nullable=False, server_default="{}")
    )
    op.add_column(
        "partner_products",
        sa.Column("assessment_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "partner_products",
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "partner_pipeline_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "provider",
            sa.String(200),
            sa.ForeignKey("partner_connections.provider"),
            nullable=False,
        ),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column(
            "product_id", sa.Uuid(), sa.ForeignKey("partner_products.id", ondelete="CASCADE")
        ),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("operation IN ('sync','assess')", name="ck_partner_pipeline_operation"),
    )
    op.create_index(
        "uq_partner_sync_active",
        "partner_pipeline_jobs",
        ["provider"],
        unique=True,
        postgresql_where=sa.text("operation = 'sync' AND status IN ('queued','running')"),
    )
    op.create_index(
        "uq_partner_assessment_active",
        "partner_pipeline_jobs",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("operation = 'assess' AND status IN ('queued','running')"),
    )
    op.create_index("ix_partner_pipeline_due", "partner_pipeline_jobs", ["status", "available_at"])


def downgrade():
    op.drop_table("partner_pipeline_jobs")
    for name in (
        "excluded",
        "assessment_revision",
        "assessment",
        "seen_generation",
        "connection_id",
    ):
        op.drop_column("partner_products", name)
    op.drop_table("partner_connections")
