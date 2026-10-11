"""Native advertising formats and product-scoped delivery restrictions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

FORMATS = "('feed','homepage_sponsorship','sponsored_content','engagement')"
SURFACES = '\'["article","topic","feed"]\'::jsonb'
AUTOMATIC_FORMATS = '\'["feed","homepage_sponsorship","engagement"]\'::jsonb'
ALL_FORMATS = '\'["feed","homepage_sponsorship","sponsored_content","engagement"]\'::jsonb'
OLD_ENTITLEMENT = "promotion_kind IS DISTINCT FROM 'ad' OR tier IN ('gold','platinum','diamond')"
NEW_ENTITLEMENT = (
    "promotion_kind IS DISTINCT FROM 'ad' OR tier IN ('silver','gold','platinum','diamond')"
)
OLD_IDENTITY = """CREATE OR REPLACE FUNCTION preserve_partner_automatic_asset_identity()
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
END $$"""
NEW_IDENTITY = OLD_IDENTITY.replace(
    "NEW.kind IS DISTINCT FROM OLD.kind OR",
    "NEW.ad_format IS DISTINCT FROM OLD.ad_format OR "
    "NEW.article_id IS DISTINCT FROM OLD.article_id OR NEW.kind IS DISTINCT FROM OLD.kind OR",
)


def upgrade():
    op.add_column(
        "articles",
        sa.Column("sponsor_names", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.create_check_constraint(
        "ck_article_sponsor_names", "articles", "jsonb_typeof(sponsor_names) = 'array'"
    )
    op.add_column(
        "partner_accounts",
        sa.Column("ad_formats", postgresql.JSONB(), nullable=False, server_default='["feed"]'),
    )
    op.create_check_constraint(
        "ck_partner_account_ad_formats",
        "partner_accounts",
        "jsonb_typeof(ad_formats) = 'array' AND jsonb_array_length(ad_formats) <= 3 AND "
        f"ad_formats <@ {AUTOMATIC_FORMATS}",
    )
    op.drop_constraint("ck_partner_promotion_entitlement", "partner_accounts", type_="check")
    op.create_check_constraint(
        "ck_partner_promotion_entitlement", "partner_accounts", NEW_ENTITLEMENT
    )
    for table, prefix in (("partner_campaigns", "campaign"), ("partner_assets", "asset")):
        op.add_column(
            table,
            sa.Column("ad_format", sa.String(30), nullable=False, server_default="feed"),
        )
        op.add_column(table, sa.Column("article_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_partner_{prefix}_article",
            table,
            "articles",
            ["article_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_check_constraint(
            f"ck_partner_{prefix}_ad_format", table, f"ad_format IN {FORMATS}"
        )
        op.create_check_constraint(
            f"ck_partner_{prefix}_article",
            table,
            "(ad_format = 'sponsored_content') = (article_id IS NOT NULL)",
        )
    op.drop_index("uq_partner_managed_campaign", table_name="partner_campaigns")
    op.create_index(
        "uq_partner_managed_campaign",
        "partner_campaigns",
        ["account_id", "ad_format"],
        unique=True,
        postgresql_where=sa.text("managed"),
    )
    op.drop_index("uq_partner_automatic_organic_asset", table_name="partner_assets")
    op.create_index(
        "uq_partner_automatic_organic_asset",
        "partner_assets",
        ["account_id", "product_id", "ad_format"],
        unique=True,
        postgresql_where=sa.text("automatic AND campaign_id IS NULL"),
    )
    op.drop_index("uq_partner_automatic_sponsored_asset", table_name="partner_assets")
    op.create_index(
        "uq_partner_automatic_sponsored_asset",
        "partner_assets",
        ["account_id", "product_id", "campaign_id", "ad_format", "article_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("automatic AND campaign_id IS NOT NULL"),
    )
    op.execute(NEW_IDENTITY)
    op.add_column(
        "partner_product_promotions",
        sa.Column("promotion_surfaces", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.add_column(
        "partner_product_promotions",
        sa.Column("ad_formats", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_partner_product_surfaces",
        "partner_product_promotions",
        "promotion_surfaces IS NULL OR (jsonb_typeof(promotion_surfaces) = 'array' AND "
        f"jsonb_array_length(promotion_surfaces) <= 3 AND promotion_surfaces <@ {SURFACES})",
    )
    op.create_check_constraint(
        "ck_partner_product_ad_formats",
        "partner_product_promotions",
        "ad_formats IS NULL OR (jsonb_typeof(ad_formats) = 'array' AND "
        f"jsonb_array_length(ad_formats) <= 4 AND ad_formats <@ {ALL_FORMATS})",
    )
    op.add_column(
        "partner_placement_deliveries", sa.Column("surface", sa.String(20), nullable=True)
    )
    op.create_check_constraint(
        "ck_partner_delivery_surface",
        "partner_placement_deliveries",
        "surface IS NULL OR surface IN ('article','topic','feed')",
    )


def downgrade():
    # An older runtime cannot enforce format/location restrictions or permanently
    # disclose sponsored articles. Retain every ledger ID and measurement, but stop
    # affected delivery and unpublish disclosed articles before removing the fields.
    op.execute("""UPDATE partner_accounts a SET status='paused'
        WHERE a.ad_formats IS DISTINCT FROM '["feed"]'::jsonb
        OR (a.tier='silver' AND a.promotion_kind IS DISTINCT FROM 'product')
        OR EXISTS (SELECT 1 FROM partner_product_promotions p WHERE p.account_id=a.id
            AND (p.ad_formats IS NOT NULL OR p.promotion_surfaces IS NOT NULL))
        OR EXISTS (SELECT 1 FROM partner_campaigns c
            WHERE c.account_id=a.id AND c.ad_format <> 'feed')
        OR EXISTS (SELECT 1 FROM partner_assets s WHERE s.account_id=a.id AND s.ad_format <> 'feed')
    """)
    op.execute("""UPDATE articles SET publication_status='unpublished',
        editorial_revision=editorial_revision+1 WHERE sponsor_names <> '[]'::jsonb""")
    op.execute(
        "UPDATE partner_campaigns SET status='paused', managed=false WHERE ad_format <> 'feed'"
    )
    op.execute("UPDATE partner_assets SET status='paused' WHERE ad_format <> 'feed'")
    # Multiple format/creative snapshots cannot satisfy the older partial unique
    # indexes. Retire their automatic status only inside this transactional downgrade.
    op.execute("DROP TRIGGER partner_automatic_asset_identity ON partner_assets")
    op.execute("UPDATE partner_assets SET automatic=false WHERE ad_format <> 'feed'")
    op.drop_index("uq_partner_automatic_sponsored_asset", table_name="partner_assets")
    op.drop_index("uq_partner_automatic_organic_asset", table_name="partner_assets")
    op.drop_index("uq_partner_managed_campaign", table_name="partner_campaigns")
    for table, prefix in (("partner_assets", "asset"), ("partner_campaigns", "campaign")):
        op.drop_constraint(f"ck_partner_{prefix}_article", table, type_="check")
        op.drop_constraint(f"ck_partner_{prefix}_ad_format", table, type_="check")
        op.drop_constraint(f"fk_partner_{prefix}_article", table, type_="foreignkey")
        op.drop_column(table, "article_id")
        op.drop_column(table, "ad_format")
    op.create_index(
        "uq_partner_managed_campaign",
        "partner_campaigns",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("managed"),
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
    op.execute(OLD_IDENTITY)
    op.execute("""CREATE TRIGGER partner_automatic_asset_identity BEFORE UPDATE ON partner_assets
        FOR EACH ROW EXECUTE FUNCTION preserve_partner_automatic_asset_identity()""")
    op.drop_constraint("ck_partner_delivery_surface", "partner_placement_deliveries", type_="check")
    op.drop_column("partner_placement_deliveries", "surface")
    op.drop_constraint("ck_partner_product_ad_formats", "partner_product_promotions", type_="check")
    op.drop_constraint("ck_partner_product_surfaces", "partner_product_promotions", type_="check")
    op.drop_column("partner_product_promotions", "ad_formats")
    op.drop_column("partner_product_promotions", "promotion_surfaces")
    op.drop_constraint("ck_partner_promotion_entitlement", "partner_accounts", type_="check")
    op.execute(
        "UPDATE partner_accounts SET promotion_kind=NULL "
        "WHERE tier='silver' AND promotion_kind='ad'"
    )
    op.create_check_constraint(
        "ck_partner_promotion_entitlement", "partner_accounts", OLD_ENTITLEMENT
    )
    op.drop_constraint("ck_partner_account_ad_formats", "partner_accounts", type_="check")
    op.drop_column("partner_accounts", "ad_formats")
    op.drop_constraint("ck_article_sponsor_names", "articles", type_="check")
    op.drop_column("articles", "sponsor_names")
