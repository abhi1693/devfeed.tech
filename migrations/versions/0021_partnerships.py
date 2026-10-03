"""API-managed launch partnerships, canonical products, and durable evaluation jobs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0021"
down_revision = "0020"
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
        sa.Column("sync_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=False, server_default="360"),
        sa.CheckConstraint(
            "sync_interval_minutes BETWEEN 1 AND 10080", name="ck_partner_sync_interval"
        ),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("updated_by", JSONB(), nullable=False),
    )
    op.create_table(
        "partner_products",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("product_url", sa.String(2048), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("pricing", sa.String(20), nullable=False),
        sa.Column("technologies", JSONB(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviews", JSONB(), nullable=False),
        sa.Column("assessment", JSONB(), nullable=False, server_default="{}"),
        sa.Column("assessment_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("metadata_listing_id", sa.Uuid()),
        sa.Column("merged_into_id", sa.Uuid(), sa.ForeignKey("partner_products.id")),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','paused','withdrawn')",
            name="ck_partner_status",
        ),
    )
    op.create_table(
        "partner_listings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("partner_products.id"), nullable=False),
        sa.Column(
            "provider",
            sa.String(200),
            sa.ForeignKey("partner_connections.provider"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("product_url", sa.String(2048), nullable=False),
        sa.Column("listing_url", sa.String(2048), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("pricing", sa.String(20), nullable=False),
        sa.Column("attribution", sa.String(300), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("identity_status", sa.String(20), nullable=False),
        sa.Column("identity_reason", sa.Text()),
        sa.Column("seen_generation", sa.Uuid()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "external_id", name="uq_partner_listing_identity"),
        sa.CheckConstraint(
            "identity_status IN ('resolved', 'unresolved')",
            name="ck_partner_listing_identity_status",
        ),
    )
    op.create_index("ix_partner_listings_product_id", "partner_listings", ["product_id"])
    op.create_foreign_key(
        "fk_partner_metadata_listing",
        "partner_products",
        "partner_listings",
        ["metadata_listing_id"],
        ["id"],
    )
    op.create_table(
        "partner_product_urls",
        sa.Column("url_hash", sa.String(64), primary_key=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("partner_products.id"), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_partner_product_urls_product_id", "partner_product_urls", ["product_id"])
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
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("partner_pipeline_jobs.id")),
        sa.Column("external_id", sa.String(200)),
        sa.UniqueConstraint("parent_id", "external_id", name="uq_partner_product_sync"),
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
        sa.CheckConstraint(
            "operation IN ('sync','sync_product','assess')", name="ck_partner_pipeline_operation"
        ),
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
    op.create_index("ix_partner_pipeline_jobs_parent_id", "partner_pipeline_jobs", ["parent_id"])
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
    op.drop_table("partner_pipeline_jobs")
    op.drop_table("partner_evaluations")
    op.drop_table("partner_product_urls")
    op.drop_constraint("fk_partner_metadata_listing", "partner_products", type_="foreignkey")
    op.drop_table("partner_listings")
    op.drop_table("partner_products")
    op.drop_table("partner_connections")
