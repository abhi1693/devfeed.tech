"""Account promotion defaults, finite paid campaigns and prepared contextual matches."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

SURFACES = '\'["article","topic","feed"]\'::jsonb'


def upgrade():
    op.add_column(
        "partner_accounts",
        sa.Column("organic_enabled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "partner_accounts",
        sa.Column("auto_include_products", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "partner_accounts",
        sa.Column(
            "promotion_surfaces",
            postgresql.JSONB(),
            nullable=False,
            server_default='["article","topic","feed"]',
        ),
    )
    op.add_column(
        "partner_accounts",
        sa.Column("frequency_cap", sa.Integer(), nullable=False, server_default="3"),
    )
    op.create_check_constraint(
        "ck_partner_frequency_cap", "partner_accounts", "frequency_cap BETWEEN 1 AND 20"
    )
    op.create_check_constraint(
        "ck_partner_promotion_surfaces",
        "partner_accounts",
        "jsonb_typeof(promotion_surfaces) = 'array' AND "
        "jsonb_array_length(promotion_surfaces) BETWEEN 1 AND 3 AND promotion_surfaces <@ "
        + SURFACES,
    )
    op.create_table(
        "partner_campaigns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("partner_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("targeting", sa.String(20), nullable=False, server_default="all"),
        sa.Column("product_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("topic_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("technologies", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "surfaces",
            postgresql.JSONB(),
            nullable=False,
            server_default='["article","topic","feed"]',
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("daily_impression_limit", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "id", name="uq_partner_campaign_account"),
        sa.CheckConstraint(
            "status IN ('draft','active','paused','ended')", name="ck_partner_campaign_status"
        ),
        sa.CheckConstraint(
            "targeting IN ('all','selected','filters')", name="ck_partner_campaign_targeting"
        ),
        sa.CheckConstraint(
            "ends_at > starts_at AND isfinite(starts_at) AND isfinite(ends_at)",
            name="ck_partner_campaign_dates",
        ),
        sa.CheckConstraint("priority BETWEEN 1 AND 100", name="ck_partner_campaign_priority"),
        sa.CheckConstraint(
            "daily_impression_limit IS NULL OR daily_impression_limit > 0",
            name="ck_partner_campaign_daily_limit",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(surfaces) = 'array' AND jsonb_array_length(surfaces) BETWEEN 1 AND "
            "3 AND surfaces <@ " + SURFACES,
            name="ck_partner_campaign_surfaces",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(product_ids) = 'array' AND jsonb_typeof(topic_ids) = 'array' AND "
            "jsonb_typeof(technologies) = 'array' AND "
            "((targeting = 'all' AND jsonb_array_length(product_ids) = 0 AND "
            "jsonb_array_length(topic_ids) = 0 AND jsonb_array_length(technologies) = 0) OR "
            "(targeting = 'selected' AND jsonb_array_length(product_ids) > 0 AND "
            "jsonb_array_length(topic_ids) = 0 AND jsonb_array_length(technologies) = 0) OR "
            "(targeting = 'filters' AND jsonb_array_length(product_ids) = 0 AND "
            "(jsonb_array_length(topic_ids) > 0 OR jsonb_array_length(technologies) > 0)))",
            name="ck_partner_campaign_selection",
        ),
    )
    op.create_index("ix_partner_campaigns_account_id", "partner_campaigns", ["account_id"])
    op.create_index(
        "ix_partner_campaign_live", "partner_campaigns", ["status", "starts_at", "ends_at"]
    )
    op.create_table(
        "partner_product_promotions",
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("partner_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "product_id",
            sa.Uuid(),
            sa.ForeignKey("partner_products.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "partner_assets",
        sa.Column("automatic", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("partner_assets", sa.Column("campaign_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_partner_asset_campaign_account",
        "partner_assets",
        "partner_campaigns",
        ["account_id", "campaign_id"],
        ["account_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_partner_assets_campaign_id", "partner_assets", ["campaign_id"])
    op.create_check_constraint(
        "ck_partner_automatic_asset_identity",
        "partner_assets",
        "NOT automatic OR (product_id IS NOT NULL AND ((campaign_id IS NULL AND kind = "
        "'product') OR (campaign_id IS NOT NULL AND kind = 'ad')))",
    )
    op.create_index(
        "uq_partner_automatic_organic_asset",
        "partner_assets",
        ["account_id", "product_id"],
        unique=True,
        postgresql_where=sa.text("automatic AND campaign_id IS NULL"),
    )
    op.create_index(
        "uq_partner_automatic_sponsored_asset",
        "partner_assets",
        ["account_id", "product_id", "campaign_id"],
        unique=True,
        postgresql_where=sa.text("automatic AND campaign_id IS NOT NULL"),
    )
    # Historical receipts and reporting keep their original commercial attribution.
    op.execute("""CREATE FUNCTION preserve_partner_automatic_asset_identity()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF OLD.automatic AND (NEW.account_id IS DISTINCT FROM OLD.account_id OR
            NEW.product_id IS DISTINCT FROM OLD.product_id OR
            NEW.campaign_id IS DISTINCT FROM OLD.campaign_id OR
            NEW.kind IS DISTINCT FROM OLD.kind OR NEW.automatic IS DISTINCT FROM OLD.automatic) THEN
            RAISE EXCEPTION 'Automatic partner asset attribution is immutable'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER partner_automatic_asset_identity BEFORE UPDATE ON partner_assets FOR "
        "EACH ROW EXECUTE FUNCTION preserve_partner_automatic_asset_identity()"
    )
    op.create_table(
        "partner_product_topics",
        sa.Column(
            "product_id",
            sa.Uuid(),
            sa.ForeignKey("partner_products.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "topic_id", sa.Uuid(), sa.ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("product_revision", sa.Integer(), nullable=False),
        sa.Column("product_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("topic_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_partner_product_topic_score", "partner_product_topics", ["topic_id", "score"]
    )
    op.create_table(
        "partner_article_matches",
        sa.Column(
            "product_id",
            sa.Uuid(),
            sa.ForeignKey("partner_products.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("product_revision", sa.Integer(), nullable=False),
        sa.Column("product_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("editorial_revision", sa.Integer(), nullable=False),
        sa.Column("article_fingerprint", sa.String(64), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False, server_default="rules"),
        sa.Column("relevant", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_partner_article_match_score",
        "partner_article_matches",
        ["article_id", "relevant", "score"],
    )
    op.create_table(
        "partner_match_states",
        sa.Column(
            "product_id",
            sa.Uuid(),
            sa.ForeignKey("partner_products.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("product_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("topic_generation", sa.String(64), nullable=False, server_default=""),
        sa.Column("article_cursor", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_partner_match_states_updated_at", "partner_match_states", ["updated_at"])
    op.create_table(
        "partner_placement_deliveries",
        sa.Column("delivery_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Uuid(),
            sa.ForeignKey("partner_assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_partner_delivery_asset_issued",
        "partner_placement_deliveries",
        ["asset_id", "issued_at"],
    )
    op.create_index(
        "ix_partner_placement_deliveries_asset_id", "partner_placement_deliveries", ["asset_id"]
    )
    op.create_index(
        "ix_partner_placement_deliveries_issued_at", "partner_placement_deliveries", ["issued_at"]
    )


def downgrade():
    for name in (
        "partner_placement_deliveries",
        "partner_match_states",
        "partner_article_matches",
        "partner_product_topics",
    ):
        op.drop_table(name)
    op.execute("DROP TRIGGER partner_automatic_asset_identity ON partner_assets")
    op.execute("DROP FUNCTION preserve_partner_automatic_asset_identity()")
    op.drop_index("uq_partner_automatic_sponsored_asset", table_name="partner_assets")
    op.drop_index("uq_partner_automatic_organic_asset", table_name="partner_assets")
    op.drop_constraint("ck_partner_automatic_asset_identity", "partner_assets", type_="check")
    op.drop_constraint("fk_partner_asset_campaign_account", "partner_assets", type_="foreignkey")
    op.drop_index("ix_partner_assets_campaign_id", table_name="partner_assets")
    op.drop_column("partner_assets", "campaign_id")
    op.drop_column("partner_assets", "automatic")
    op.drop_table("partner_product_promotions")
    op.drop_table("partner_campaigns")
    op.drop_constraint("ck_partner_promotion_surfaces", "partner_accounts", type_="check")
    op.drop_constraint("ck_partner_frequency_cap", "partner_accounts", type_="check")
    for name in ("frequency_cap", "promotion_surfaces", "auto_include_products", "organic_enabled"):
        op.drop_column("partner_accounts", name)
