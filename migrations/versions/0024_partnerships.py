"""Partner catalog, account policy, attribution, and targeted contextual matching.

This is the single development-branch migration from the deployed 0023 schema.
All table definitions, constraints, and SQL functions are frozen in this revision.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

PRODUCT_FIELDS = (
    "revision,updated_at,technologies,evidence,name,tagline,status,"
    "verified_at,excluded,merged_into_id"
)

PRODUCT_FACT_FIELDS = "revision,technologies,evidence,name,tagline"

ARTICLE_FIELDS = "title,summary,content_type,publication_status,editorial_revision"

TOPIC_FIELDS = "name,aliases,status,identity_keys,updated_at"

BASE_IMAGE_JOB_SUBJECT = (
    "(article_id IS NOT NULL AND topic_id IS NULL AND source_id IS NULL "
    "AND operation NOT IN ('topic-logo','source-logo','source-logo-refresh')) OR "
    "(article_id IS NULL AND topic_id IS NOT NULL AND source_id IS NULL "
    "AND operation = 'topic-logo') OR "
    "(article_id IS NULL AND topic_id IS NULL AND source_id IS NOT NULL "
    "AND operation IN ('source-logo','source-logo-refresh'))"
)
IMAGE_JOB_SUBJECT = (
    "(article_id IS NOT NULL AND topic_id IS NULL AND source_id IS NULL AND product_id IS NULL "
    "AND account_id IS NULL AND subject_revision IS NULL AND operation NOT IN "
    "('topic-logo','source-logo','source-logo-refresh','product-logo','product-image',"
    "'product-ad-image')) OR "
    "(article_id IS NULL AND topic_id IS NOT NULL AND source_id IS NULL AND product_id IS NULL "
    "AND account_id IS NULL AND subject_revision IS NULL AND operation = 'topic-logo') OR "
    "(article_id IS NULL AND topic_id IS NULL AND source_id IS NOT NULL AND product_id IS NULL "
    "AND account_id IS NULL AND subject_revision IS NULL "
    "AND operation IN ('source-logo','source-logo-refresh')) OR "
    "(article_id IS NULL AND topic_id IS NULL AND source_id IS NULL AND product_id IS NOT NULL "
    "AND account_id IS NULL AND subject_revision IS NULL "
    "AND operation IN ('product-logo','product-image')) OR "
    "(article_id IS NULL AND topic_id IS NULL AND source_id IS NULL AND product_id IS NOT NULL "
    "AND account_id IS NOT NULL AND subject_revision IS NOT NULL AND subject_revision >= 0 "
    "AND operation = 'product-ad-image')"
)
AUTOMATIC_ASSET_IDENTITY = """CREATE FUNCTION preserve_partner_automatic_asset_identity()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.automatic AND (
        NEW.account_id IS DISTINCT FROM OLD.account_id OR
        NEW.product_id IS DISTINCT FROM OLD.product_id OR
        NEW.campaign_id IS DISTINCT FROM OLD.campaign_id OR
        NEW.ad_format IS DISTINCT FROM OLD.ad_format OR
        NEW.article_id IS DISTINCT FROM OLD.article_id OR
        NEW.kind IS DISTINCT FROM OLD.kind OR
        NEW.automatic IS DISTINCT FROM OLD.automatic
    ) THEN
        RAISE EXCEPTION 'Automatic partner asset attribution is immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$"""


def upgrade():
    op.create_table(
        "partner_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("tier", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organic_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("auto_include_products", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "promotion_surfaces",
            postgresql.JSONB(),
            server_default='["article","topic","feed"]',
            nullable=False,
        ),
        sa.Column("promotion_kind", sa.String(20), nullable=True),
        sa.Column("approval_mode", sa.String(20), server_default="qualified", nullable=False),
        sa.Column(
            "outbound_destination",
            sa.String(20),
            server_default="product_url",
            nullable=False,
        ),
        sa.Column("audience", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column(
            "ad_formats",
            postgresql.JSONB(),
            server_default='["feed"]',
            nullable=False,
        ),
        sa.CheckConstraint(
            "approval_mode IN ('qualified','trusted')", name="ck_partner_approval_mode"
        ),
        sa.CheckConstraint("jsonb_typeof(audience) = 'object'", name="ck_partner_account_audience"),
        sa.CheckConstraint(
            "outbound_destination IN ('product_url','listing_url')",
            name="ck_partner_outbound_destination",
        ),
        sa.CheckConstraint(
            (
                "promotion_kind IS DISTINCT FROM 'ad' OR tier IN "
                "('silver','gold','platinum','diamond')"
            ),
            name="ck_partner_promotion_entitlement",
        ),
        sa.CheckConstraint(
            "promotion_kind IS NULL OR promotion_kind IN ('product','ad')",
            name="ck_partner_promotion_kind",
        ),
        sa.CheckConstraint("status IN ('active','paused')", name="ck_partner_account_status"),
        sa.CheckConstraint(
            "tier IN ('bronze','silver','gold','platinum','diamond')",
            name="ck_partner_account_tier",
        ),
        sa.CheckConstraint(
            (
                "jsonb_typeof(ad_formats) = 'array' AND "
                "jsonb_array_length(ad_formats) <= 3 AND ad_formats <@ "
                '\'["feed","homepage_sponsorship","engagement"]\'::jsonb'
            ),
            name="ck_partner_account_ad_formats",
        ),
        sa.CheckConstraint(
            (
                "jsonb_typeof(promotion_surfaces) = 'array' AND "
                "jsonb_array_length(promotion_surfaces) BETWEEN 1 AND 3 AND "
                'promotion_surfaces <@ \'["article","topic","feed"]\'::jsonb'
            ),
            name="ck_partner_promotion_surfaces",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "partner_match_jobs",
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("cursor", sa.Uuid(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('product','article','topics')", name="ck_partner_match_job_kind"
        ),
        sa.CheckConstraint("generation > 0", name="ck_partner_match_job_generation"),
        sa.PrimaryKeyConstraint("kind", "entity_id"),
    )
    op.create_index(
        "ix_partner_match_job_due",
        "partner_match_jobs",
        ["updated_at", "kind", "entity_id"],
        unique=False,
    )
    op.create_table(
        "partner_products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("product_url", sa.String(2048), nullable=False),
        sa.Column("tagline", sa.String(160), nullable=False),
        sa.Column("pricing", sa.String(20), nullable=False),
        sa.Column("technologies", postgresql.JSONB(), nullable=False),
        sa.Column(
            "matching_tokens", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column(
            "managed_logo",
            postgresql.JSONB(),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "managed_image",
            postgresql.JSONB(),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviews", postgresql.JSONB(), nullable=False),
        sa.Column(
            "assessment",
            postgresql.JSONB(),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("assessment_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("excluded", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("metadata_listing_id", sa.Uuid(), nullable=True),
        sa.Column("metadata_account_id", sa.Uuid(), nullable=True),
        sa.Column("merged_into_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','paused','withdrawn')",
            name="ck_partner_status",
        ),
        sa.CheckConstraint(
            "metadata_listing_id IS NULL OR metadata_account_id IS NULL",
            name="ck_partner_metadata_authority",
        ),
        sa.ForeignKeyConstraint(
            ["metadata_account_id"], ["partner_accounts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["merged_into_id"],
            ["partner_products.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_partner_products_matching_tokens",
        "partner_products",
        ["matching_tokens"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "partner_manual_products",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["partner_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["partner_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "product_id"),
    )
    op.create_index(
        "ix_partner_manual_products_product_active",
        "partner_manual_products",
        ["product_id", "active"],
    )
    op.create_table(
        "partner_article_matches",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("product_revision", sa.Integer(), nullable=False),
        sa.Column("product_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("editorial_revision", sa.Integer(), nullable=False),
        sa.Column("article_fingerprint", sa.String(64), nullable=False),
        sa.Column("origin", sa.String(20), server_default="rules", nullable=False),
        sa.Column("relevant", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["partner_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("product_id", "article_id"),
    )
    op.create_index(
        "ix_partner_article_match_score",
        "partner_article_matches",
        ["article_id", "relevant", "score"],
        unique=False,
    )
    op.create_table(
        "partner_campaigns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("targeting", sa.String(20), server_default="all", nullable=False),
        sa.Column("ad_format", sa.String(30), server_default="feed", nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=True),
        sa.Column(
            "product_ids",
            postgresql.JSONB(),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "topic_ids",
            postgresql.JSONB(),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "technologies",
            postgresql.JSONB(),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "audience", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "surfaces",
            postgresql.JSONB(),
            server_default='["article","topic","feed"]',
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="50", nullable=False),
        sa.Column("daily_impression_limit", sa.BigInteger(), nullable=True),
        sa.Column("managed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(ad_format = 'sponsored_content') = (article_id IS NOT NULL)",
            name="ck_partner_campaign_article",
        ),
        sa.CheckConstraint(
            "ad_format IN ('feed','homepage_sponsorship','sponsored_content','engagement')",
            name="ck_partner_campaign_ad_format",
        ),
        sa.CheckConstraint(
            "audience IS NULL OR jsonb_typeof(audience) = 'object'",
            name="ck_partner_campaign_audience",
        ),
        sa.CheckConstraint(
            (
                "jsonb_typeof(product_ids) = 'array' AND jsonb_typeof(topic_ids) "
                "= 'array' AND jsonb_typeof(technologies) = 'array' AND "
                "((targeting = 'all' AND jsonb_array_length(product_ids) = 0 AND "
                "jsonb_array_length(topic_ids) = 0 AND "
                "jsonb_array_length(technologies) = 0) OR (targeting = 'selected' "
                "AND jsonb_array_length(product_ids) > 0 AND "
                "jsonb_array_length(topic_ids) = 0 AND "
                "jsonb_array_length(technologies) = 0) OR (targeting = 'filters' "
                "AND jsonb_array_length(product_ids) = 0 AND "
                "(jsonb_array_length(topic_ids) > 0 OR "
                "jsonb_array_length(technologies) > 0)))"
            ),
            name="ck_partner_campaign_selection",
        ),
        sa.CheckConstraint(
            "status IN ('draft','active','paused','ended')", name="ck_partner_campaign_status"
        ),
        sa.CheckConstraint(
            "targeting IN ('all','selected','filters')", name="ck_partner_campaign_targeting"
        ),
        sa.CheckConstraint(
            "daily_impression_limit IS NULL OR daily_impression_limit > 0",
            name="ck_partner_campaign_daily_limit",
        ),
        sa.CheckConstraint(
            "ends_at > starts_at AND isfinite(starts_at) AND isfinite(ends_at)",
            name="ck_partner_campaign_dates",
        ),
        sa.CheckConstraint(
            (
                "jsonb_typeof(surfaces) = 'array' AND "
                "jsonb_array_length(surfaces) BETWEEN 1 AND 3 AND surfaces <@ "
                '\'["article","topic","feed"]\'::jsonb'
            ),
            name="ck_partner_campaign_surfaces",
        ),
        sa.CheckConstraint("priority BETWEEN 1 AND 100", name="ck_partner_campaign_priority"),
        sa.ForeignKeyConstraint(["account_id"], ["partner_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"], name="fk_partner_campaign_article", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "id", name="uq_partner_campaign_account"),
    )
    op.create_index(
        "ix_partner_campaign_live",
        "partner_campaigns",
        ["status", "starts_at", "ends_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_partner_campaigns_account_id"), "partner_campaigns", ["account_id"], unique=False
    )
    op.create_index(
        "uq_partner_managed_campaign",
        "partner_campaigns",
        ["account_id", "ad_format"],
        unique=True,
        postgresql_where=sa.text("managed"),
    )
    op.create_table(
        "partner_connections",
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column(
            "partnership_type",
            sa.String(40),
            server_default="launch_platform",
            nullable=False,
        ),
        sa.Column("provider", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), server_default="", nullable=False),
        sa.Column(
            "connector",
            postgresql.JSONB(),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("encrypted_credential", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("sync_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("sync_interval_minutes", sa.Integer(), server_default="360", nullable=False),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_by", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "partnership_type = 'launch_platform'", name="ck_partner_connection_type"
        ),
        sa.CheckConstraint(
            "sync_interval_minutes BETWEEN 1 AND 10080", name="ck_partner_sync_interval"
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["partner_accounts.id"],
            name="fk_partner_connection_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("provider"),
    )
    op.create_index(
        op.f("ix_partner_connections_account_id"),
        "partner_connections",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        "ix_partner_connections_active_account",
        "partner_connections",
        ["account_id", "name", "provider"],
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_table(
        "partner_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("reviews", postgresql.JSONB(), nullable=False),
        sa.Column("requested_by", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["partner_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_partner_evaluation_created", "partner_evaluations", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_partner_evaluations_product_id"),
        "partner_evaluations",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        "uq_partner_evaluation_active",
        "partner_evaluations",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_table(
        "partner_memberships",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.String(2048), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["partner_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "issuer", "subject"),
    )
    op.create_index(
        "ix_partner_memberships_identity_accounts",
        "partner_memberships",
        ["issuer", "subject", "account_id"],
        unique=False,
    )
    op.create_table(
        "partner_product_promotions",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("excluded", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "promotion_surfaces",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "ad_formats", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("ad_image_url", sa.String(2048), nullable=True),
        sa.Column("managed_ad_image", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("ad_image_revision", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("ad_image_revision >= 0", name="ck_partner_ad_image_revision"),
        sa.Column("commercial_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_source", sa.String(20), nullable=True),
        sa.CheckConstraint(
            (
                "(commercial_approved_at IS NULL AND approval_source IS NULL) OR "
                "(commercial_approved_at IS NOT NULL AND approval_source IS NOT "
                "NULL AND approval_source = 'trusted')"
            ),
            name="ck_partner_commercial_approval",
        ),
        sa.CheckConstraint(
            (
                "ad_formats IS NULL OR (jsonb_typeof(ad_formats) = 'array' AND "
                "jsonb_array_length(ad_formats) <= 4 AND ad_formats <@ "
                '\'["feed","homepage_sponsorship","sponsored_content","engagement"]'
                "'::jsonb)"
            ),
            name="ck_partner_product_ad_formats",
        ),
        sa.CheckConstraint(
            (
                "promotion_surfaces IS NULL OR (jsonb_typeof(promotion_surfaces) "
                "= 'array' AND jsonb_array_length(promotion_surfaces) <= 3 AND "
                'promotion_surfaces <@ \'["article","topic","feed"]\'::jsonb)'
            ),
            name="ck_partner_product_surfaces",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["partner_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["partner_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "product_id"),
    )
    op.create_table(
        "partner_product_topics",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("product_revision", sa.Integer(), nullable=False),
        sa.Column("product_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("topic_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["partner_products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("product_id", "topic_id"),
    )
    op.create_index(
        "ix_partner_product_topic_score",
        "partner_product_topics",
        ["topic_id", "score"],
        unique=False,
    )
    op.create_table(
        "partner_product_urls",
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["partner_products.id"],
        ),
        sa.PrimaryKeyConstraint("url_hash"),
    )
    op.create_index(
        op.f("ix_partner_product_urls_product_id"),
        "partner_product_urls",
        ["product_id"],
        unique=False,
    )
    op.create_table(
        "partner_promotion_enrollments",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("product_cursor", sa.Uuid(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sweep_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["partner_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
    )
    op.create_index(
        "ix_partner_promotion_enrollment_due",
        "partner_promotion_enrollments",
        ["available_at", "updated_at", "account_id"],
        unique=False,
    )
    op.create_table(
        "partner_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("automatic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("ad_format", sa.String(30), server_default="feed", nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(ad_format = 'sponsored_content') = (article_id IS NOT NULL)",
            name="ck_partner_asset_article",
        ),
        sa.CheckConstraint(
            (
                "NOT automatic OR (product_id IS NOT NULL AND ((campaign_id IS "
                "NULL AND kind = 'product') OR (campaign_id IS NOT NULL AND kind "
                "= 'ad')))"
            ),
            name="ck_partner_automatic_asset_identity",
        ),
        sa.CheckConstraint(
            "ad_format IN ('feed','homepage_sponsorship','sponsored_content','engagement')",
            name="ck_partner_asset_ad_format",
        ),
        sa.CheckConstraint(
            "kind != 'product' OR product_id IS NOT NULL", name="ck_partner_asset_product"
        ),
        sa.CheckConstraint("kind IN ('product','ad')", name="ck_partner_asset_kind"),
        sa.CheckConstraint(
            "status IN ('draft','active','paused','ended')", name="ck_partner_asset_status"
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "campaign_id"],
            ["partner_campaigns.account_id", "partner_campaigns.id"],
            name="fk_partner_asset_campaign_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["partner_accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"], name="fk_partner_asset_article", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["partner_products.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_partner_asset_product", "partner_assets", ["product_id", "id"], unique=False
    )
    op.create_index(
        op.f("ix_partner_assets_account_id"), "partner_assets", ["account_id"], unique=False
    )
    op.create_index(
        op.f("ix_partner_assets_campaign_id"), "partner_assets", ["campaign_id"], unique=False
    )
    op.create_index(
        "uq_partner_automatic_organic_asset",
        "partner_assets",
        ["account_id", "product_id", "ad_format"],
        unique=True,
        postgresql_where=sa.text("automatic AND campaign_id IS NULL"),
    )
    op.create_index(
        "uq_partner_automatic_sponsored_asset",
        "partner_assets",
        ["account_id", "product_id", "campaign_id", "ad_format", "article_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("automatic AND campaign_id IS NOT NULL"),
    )
    op.create_table(
        "partner_listings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(200), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("product_url", sa.String(2048), nullable=False),
        sa.Column("listing_url", sa.String(2048), nullable=False),
        sa.Column("tagline", sa.String(160), nullable=False),
        sa.Column("pricing", sa.String(20), nullable=False),
        sa.Column("attribution", sa.String(300), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("identity_status", sa.String(20), nullable=False),
        sa.Column("identity_reason", sa.Text(), nullable=True),
        sa.Column("seen_generation", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "identity_status IN ('resolved', 'unresolved')",
            name="ck_partner_listing_identity_status",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["partner_products.id"],
        ),
        sa.ForeignKeyConstraint(
            ["provider"],
            ["partner_connections.provider"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "external_id", name="uq_partner_listing_identity"),
    )
    op.create_index(
        op.f("ix_partner_listings_product_id"), "partner_listings", ["product_id"], unique=False
    )
    op.create_table(
        "partner_pipeline_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(200), nullable=True),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("external_id", sa.String(200), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "operation IN ('sync','sync_product','assess')", name="ck_partner_pipeline_operation"
        ),
        sa.CheckConstraint(
            "operation = 'assess' OR provider IS NOT NULL", name="ck_partner_pipeline_provider"
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["partner_pipeline_jobs.id"],
        ),
        sa.ForeignKeyConstraint(["product_id"], ["partner_products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["provider"],
            ["partner_connections.provider"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parent_id", "external_id", name="uq_partner_product_sync"),
    )
    op.create_index(
        "ix_partner_pipeline_due", "partner_pipeline_jobs", ["status", "available_at"], unique=False
    )
    op.create_index(
        op.f("ix_partner_pipeline_jobs_parent_id"),
        "partner_pipeline_jobs",
        ["parent_id"],
        unique=False,
    )
    op.create_index(
        "uq_partner_assessment_active",
        "partner_pipeline_jobs",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("operation = 'assess' AND status IN ('queued','running')"),
    )
    op.create_index(
        "uq_partner_sync_active",
        "partner_pipeline_jobs",
        ["provider"],
        unique=True,
        postgresql_where=sa.text("operation = 'sync' AND status IN ('queued','running')"),
    )
    op.create_table(
        "partner_daily_metrics",
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("impressions", sa.BigInteger(), nullable=False),
        sa.Column("clicks", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "impressions >= 0 AND clicks >= 0", name="ck_partner_metric_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["partner_assets.id"],
        ),
        sa.PrimaryKeyConstraint("asset_id", "day"),
    )
    op.create_table(
        "partner_delivery_events",
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.CheckConstraint("kind IN ('impression','click')", name="ck_partner_delivery_kind"),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["partner_assets.id"],
        ),
        sa.PrimaryKeyConstraint("asset_id", "delivery_id", "kind"),
    )
    op.create_index(
        "ix_partner_delivery_event_day", "partner_delivery_events", ["day"], unique=False
    )
    op.create_table(
        "partner_placement_deliveries",
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("surface", sa.String(20), nullable=True),
        sa.CheckConstraint(
            "surface IS NULL OR surface IN ('article','topic','feed')",
            name="ck_partner_delivery_surface",
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["partner_assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("delivery_id"),
    )
    op.create_index(
        "ix_partner_delivery_asset_issued",
        "partner_placement_deliveries",
        ["asset_id", "issued_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_partner_placement_deliveries_asset_id"),
        "partner_placement_deliveries",
        ["asset_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_partner_placement_deliveries_issued_at"),
        "partner_placement_deliveries",
        ["issued_at"],
        unique=False,
    )
    op.create_table(
        "partner_tracked_daily_metrics",
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("impressions", sa.BigInteger(), nullable=False),
        sa.Column("clicks", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "impressions >= 0 AND clicks >= 0", name="ck_partner_tracked_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["partner_assets.id"],
        ),
        sa.PrimaryKeyConstraint("asset_id", "day"),
    )
    op.create_foreign_key(
        "fk_partner_metadata_listing",
        "partner_products",
        "partner_listings",
        ["metadata_listing_id"],
        ["id"],
    )
    op.add_column(
        "articles",
        sa.Column("sponsor_names", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.create_check_constraint(
        "ck_article_sponsor_names", "articles", "jsonb_typeof(sponsor_names) = 'array'"
    )
    op.add_column(
        "article_image_jobs",
        sa.Column(
            "product_id", sa.Uuid(), sa.ForeignKey("partner_products.id", ondelete="CASCADE")
        ),
    )
    op.add_column(
        "article_image_jobs",
        sa.Column(
            "account_id", sa.Uuid(), sa.ForeignKey("partner_accounts.id", ondelete="CASCADE")
        ),
    )
    op.add_column("article_image_jobs", sa.Column("subject_revision", sa.Integer(), nullable=True))
    op.create_index("ix_article_image_jobs_account_id", "article_image_jobs", ["account_id"])
    op.create_index(
        "uq_partner_ad_image_active",
        "article_image_jobs",
        ["account_id", "product_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running') AND operation='product-ad-image'"),
    )
    op.create_index(
        "ix_partner_ad_image_history",
        "article_image_jobs",
        ["account_id", "product_id", "subject_revision", "created_at"],
        postgresql_where=sa.text("operation='product-ad-image'"),
    )
    op.create_index("ix_article_image_jobs_product_id", "article_image_jobs", ["product_id"])
    op.create_index(
        "uq_product_image_active",
        "article_image_jobs",
        ["product_id", "operation"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running') AND account_id IS NULL"),
    )
    op.drop_constraint("ck_image_job_subject", "article_image_jobs", type_="check")
    op.create_check_constraint("ck_image_job_subject", "article_image_jobs", IMAGE_JOB_SUBJECT)
    op.execute(AUTOMATIC_ASSET_IDENTITY)
    op.execute("""CREATE TRIGGER partner_automatic_asset_identity BEFORE UPDATE ON partner_assets
        FOR EACH ROW EXECUTE FUNCTION preserve_partner_automatic_asset_identity()""")
    for table in ("articles", "topics"):
        op.add_column(
            table,
            sa.Column(
                "matching_tokens", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"
            ),
        )
    op.create_index(
        "ix_topic_relations_related_topic", "topic_relations", ["related_topic_id", "topic_id"]
    )

    op.execute("""
    CREATE FUNCTION partner_matching_tokens(value text) RETURNS text[]
    LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
      SELECT ARRAY(
        SELECT DISTINCT token FROM (
          SELECT (regexp_matches(lower(translate(coalesce(value, ''), 'İıſK', 'iisk') COLLATE "C"),
                                 '[a-z0-9_]{1,200}', 'g'))[1] AS token
          UNION ALL
          SELECT '__unicode__'
            WHERE octet_length(convert_to(coalesce(value, ''), 'UTF8'))
                  <> char_length(coalesce(value, ''))
        ) AS tokens ORDER BY token
      )
    $$
    """)

    op.execute("""
    CREATE FUNCTION partner_matching_product_tokens(value jsonb) RETURNS text[]
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
    DECLARE technology text; current_tokens text[]; tokens text[] := '{}';
    BEGIN
      IF value IS NULL THEN RETURN tokens; END IF;
      IF jsonb_typeof(value) <> 'array' THEN RETURN ARRAY['__any__']; END IF;
      FOR technology IN SELECT jsonb_array_elements_text(value) LOOP
        current_tokens := partner_matching_tokens(technology);
        IF cardinality(current_tokens) = 0 OR '__unicode__' = ANY(current_tokens) THEN
          tokens := tokens || ARRAY['__any__'];
        END IF;
        tokens := tokens || array_remove(current_tokens, '__unicode__');
      END LOOP;
      RETURN ARRAY(SELECT DISTINCT token FROM unnest(tokens) AS token ORDER BY token);
    END $$
    """)

    op.execute("""
    CREATE FUNCTION partner_matching_changed(previous jsonb, next_row jsonb, fields text[])
    RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
      SELECT EXISTS(
        SELECT 1 FROM unnest(fields) AS field
          WHERE previous->field IS DISTINCT FROM next_row->field
      )
    $$
    """)

    op.execute("""
    CREATE FUNCTION partner_matching_enqueue(job_kind text, identifier uuid) RETURNS void
    LANGUAGE sql AS $$
      INSERT INTO partner_match_jobs(kind, entity_id) VALUES (job_kind, identifier)
      ON CONFLICT (kind, entity_id) DO UPDATE SET
        generation = partner_match_jobs.generation + 1,
        cursor = NULL, requested_at = clock_timestamp(), updated_at = clock_timestamp()
    $$
    """)

    op.execute("""
    CREATE FUNCTION partner_matching_project() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME = 'partner_products' THEN
        NEW.matching_tokens := partner_matching_product_tokens(NEW.technologies);
        IF TG_OP = 'UPDATE'
           AND partner_matching_changed(to_jsonb(OLD), to_jsonb(NEW),
                                        string_to_array(TG_ARGV[0], ','))
           AND NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at THEN
          NEW.updated_at := clock_timestamp();
        END IF;
      ELSE
        NEW.matching_tokens := partner_matching_tokens(
          coalesce(NEW.title, '') || E'\\n' || coalesce(NEW.summary, ''));
      END IF;
      RETURN NEW;
    END $$
    """)

    op.execute("""
    CREATE FUNCTION partner_matching_source_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'UPDATE'
         AND NOT partner_matching_changed(to_jsonb(OLD), to_jsonb(NEW),
                                          string_to_array(TG_ARGV[1], ',')) THEN
        RETURN NEW;
      END IF;
      PERFORM partner_matching_enqueue(
        TG_ARGV[0], CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END);
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$
    """)

    op.execute("""
    CREATE FUNCTION partner_matching_taxonomy_barrier() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM pg_advisory_xact_lock(832604285);
      RETURN NULL;
    END $$
    """)

    op.execute("""
    CREATE FUNCTION partner_matching_invalidate_topics(topic_ids uuid[], tokens text[])
    RETURNS void LANGUAGE plpgsql AS $$
    BEGIN
      WITH affected AS MATERIALIZED (
        SELECT id FROM partner_products WHERE matching_tokens && tokens
        UNION
        SELECT id FROM partner_products WHERE matching_tokens @> ARRAY['__any__']::text[]
        UNION
        SELECT id FROM partner_products WHERE '__unicode__' = ANY(tokens)
        UNION
        SELECT product_id FROM partner_product_topics WHERE topic_id = ANY(topic_ids)
      ), removed AS (
        DELETE FROM partner_product_topics
          WHERE product_id IN (SELECT id FROM affected) RETURNING product_id
      )
      INSERT INTO partner_match_jobs(kind, entity_id)
        SELECT 'topics', id FROM affected ORDER BY id
      ON CONFLICT (kind, entity_id) DO UPDATE SET
        generation = partner_match_jobs.generation + 1,
        cursor = NULL, requested_at = clock_timestamp(), updated_at = clock_timestamp();
    END $$
    """)

    op.execute("""
    CREATE FUNCTION partner_matching_topic_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE identifiers uuid[] := '{}'; tokens text[] := '{}';
    BEGIN
      IF TG_OP <> 'DELETE' THEN
        NEW.matching_tokens := partner_matching_tokens(
          coalesce(NEW.name, '') || E'\\n' || coalesce(array_to_string(NEW.aliases, E'\\n'), ''));
      END IF;
      IF TG_OP = 'UPDATE' THEN
        IF NOT partner_matching_changed(to_jsonb(OLD), to_jsonb(NEW),
                                        string_to_array(TG_ARGV[0], ',')) THEN
          RETURN NEW;
        END IF;
        IF NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at THEN
          NEW.updated_at := clock_timestamp();
        END IF;
      END IF;
      IF TG_OP <> 'INSERT' THEN
        identifiers := identifiers || OLD.id;
        tokens := tokens || partner_matching_tokens(
          coalesce(OLD.name, '') || E'\\n' || coalesce(array_to_string(OLD.aliases, E'\\n'), ''));
      END IF;
      IF TG_OP <> 'DELETE' THEN
        identifiers := identifiers || NEW.id;
        tokens := tokens || NEW.matching_tokens;
      END IF;
      -- A formerly inactive/broad related target can become useful without having
      -- prepared edges yet. Include both directions before DELETE cascades lose them.
      identifiers := ARRAY(
        SELECT unnest(identifiers)
        UNION
        SELECT topic_id FROM topic_relations
          WHERE topic_id = ANY(identifiers) OR related_topic_id = ANY(identifiers)
        UNION
        SELECT related_topic_id FROM topic_relations
          WHERE topic_id = ANY(identifiers) OR related_topic_id = ANY(identifiers)
      );
      tokens := tokens || ARRAY(
        SELECT DISTINCT unnest(matching_tokens) FROM topics WHERE id = ANY(identifiers)
      );
      PERFORM partner_matching_invalidate_topics(identifiers, tokens);
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$
    """)

    op.execute("""
    CREATE FUNCTION partner_matching_relation_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE identifiers uuid[] := '{}'; tokens text[];
    BEGIN
      IF TG_OP = 'UPDATE'
         AND NOT partner_matching_changed(to_jsonb(OLD), to_jsonb(NEW),
                                          ARRAY['topic_id','related_topic_id','relation']) THEN
        RETURN NEW;
      END IF;
      IF TG_OP <> 'INSERT' THEN
        identifiers := identifiers || ARRAY[OLD.topic_id, OLD.related_topic_id];
      END IF;
      IF TG_OP <> 'DELETE' THEN
        identifiers := identifiers || ARRAY[NEW.topic_id, NEW.related_topic_id];
      END IF;
      tokens := ARRAY(
        SELECT DISTINCT unnest(matching_tokens) FROM topics WHERE id = ANY(identifiers)
      );
      PERFORM partner_matching_invalidate_topics(identifiers, tokens);
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$
    """)

    # Existing reader content must be indexed before change-capture triggers begin.
    # Partner tables are new and empty, so no historical partner conversion runs.
    op.execute("""
    UPDATE articles SET matching_tokens = partner_matching_tokens(
      coalesce(title, '') || E'\\n' || coalesce(summary, ''))
    """)
    op.execute("""
    UPDATE topics SET matching_tokens = partner_matching_tokens(
      coalesce(name, '') || E'\\n' || coalesce(array_to_string(aliases, E'\\n'), ''))
    """)
    for table in ("articles", "topics"):
        op.create_index(
            f"ix_{table}_matching_tokens", table, ["matching_tokens"], postgresql_using="gin"
        )

    for table, kind, fields in (
        ("partner_products", "product", PRODUCT_FIELDS),
        ("articles", "article", ARTICLE_FIELDS),
    ):
        # Policy changes still queue work, but must preserve the catalog observation
        # timestamp unless matching facts themselves changed without an explicit stamp.
        projection_fields = PRODUCT_FACT_FIELDS if kind == "product" else fields
        op.execute(f"""
        CREATE TRIGGER partner_matching_project
        BEFORE INSERT OR UPDATE ON {table}
        FOR EACH ROW EXECUTE FUNCTION partner_matching_project('{projection_fields}')
        """)
        op.execute(f"""
        CREATE TRIGGER partner_matching_source_changed
        AFTER INSERT OR UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION partner_matching_source_changed('{kind}', '{fields}')
        """)
    for table in ("topics", "topic_relations"):
        op.execute(f"""
        CREATE TRIGGER partner_matching_taxonomy_barrier
        BEFORE INSERT OR UPDATE OR DELETE ON {table}
        FOR EACH STATEMENT EXECUTE FUNCTION partner_matching_taxonomy_barrier()
        """)
    op.execute(f"""
    CREATE TRIGGER partner_matching_topic_changed
    BEFORE INSERT OR UPDATE OR DELETE ON topics
    FOR EACH ROW EXECUTE FUNCTION partner_matching_topic_changed('{TOPIC_FIELDS}')
    """)
    op.execute("""
    CREATE TRIGGER partner_matching_relation_changed
    BEFORE INSERT OR UPDATE OR DELETE ON topic_relations
    FOR EACH ROW EXECUTE FUNCTION partner_matching_relation_changed()
    """)


def downgrade():
    for table in ("partner_products", "articles"):
        op.execute(f"DROP TRIGGER partner_matching_source_changed ON {table}")
        op.execute(f"DROP TRIGGER partner_matching_project ON {table}")
    op.execute("DROP TRIGGER partner_matching_topic_changed ON topics")
    op.execute("DROP TRIGGER partner_matching_relation_changed ON topic_relations")
    for table in ("topics", "topic_relations"):
        op.execute(f"DROP TRIGGER partner_matching_taxonomy_barrier ON {table}")
    for function in (
        "partner_matching_relation_changed()",
        "partner_matching_topic_changed()",
        "partner_matching_invalidate_topics(uuid[], text[])",
        "partner_matching_taxonomy_barrier()",
        "partner_matching_source_changed()",
        "partner_matching_project()",
        "partner_matching_enqueue(text, uuid)",
        "partner_matching_changed(jsonb, jsonb, text[])",
        "partner_matching_product_tokens(jsonb)",
        "partner_matching_tokens(text)",
    ):
        op.execute(f"DROP FUNCTION {function}")
    for table in ("articles", "topics"):
        op.drop_index(f"ix_{table}_matching_tokens", table_name=table)
        op.drop_column(table, "matching_tokens")
    op.drop_index("ix_topic_relations_related_topic", table_name="topic_relations")
    # Older readers cannot display the permanent sponsor disclosure after it is removed.
    op.execute("""UPDATE articles SET publication_status='unpublished',
        editorial_revision=editorial_revision+1 WHERE sponsor_names <> '[]'::jsonb""")
    op.drop_constraint("ck_article_sponsor_names", "articles", type_="check")
    op.drop_column("articles", "sponsor_names")
    # Existing article/topic/source jobs remain valid under the 0023 subject policy.
    op.execute("DELETE FROM article_image_jobs WHERE product_id IS NOT NULL")
    op.drop_constraint("ck_image_job_subject", "article_image_jobs", type_="check")
    op.create_check_constraint("ck_image_job_subject", "article_image_jobs", BASE_IMAGE_JOB_SUBJECT)
    op.drop_index("uq_product_image_active", table_name="article_image_jobs")
    op.drop_index("ix_article_image_jobs_product_id", table_name="article_image_jobs")
    op.drop_index("uq_partner_ad_image_active", table_name="article_image_jobs")
    op.drop_index("ix_partner_ad_image_history", table_name="article_image_jobs")
    op.drop_index("ix_article_image_jobs_account_id", table_name="article_image_jobs")
    op.drop_column("article_image_jobs", "account_id")
    op.drop_column("article_image_jobs", "subject_revision")
    op.drop_column("article_image_jobs", "product_id")
    op.execute("DROP TRIGGER partner_automatic_asset_identity ON partner_assets")
    op.execute("DROP FUNCTION preserve_partner_automatic_asset_identity()")
    op.drop_constraint("fk_partner_metadata_listing", "partner_products", type_="foreignkey")
    op.drop_table("partner_tracked_daily_metrics")
    op.drop_table("partner_placement_deliveries")
    op.drop_table("partner_delivery_events")
    op.drop_table("partner_daily_metrics")
    op.drop_table("partner_pipeline_jobs")
    op.drop_table("partner_listings")
    op.drop_table("partner_assets")
    op.drop_table("partner_promotion_enrollments")
    op.drop_table("partner_product_urls")
    op.drop_table("partner_product_topics")
    op.drop_table("partner_product_promotions")
    op.drop_table("partner_memberships")
    op.drop_table("partner_evaluations")
    op.drop_table("partner_connections")
    op.drop_table("partner_campaigns")
    op.drop_table("partner_article_matches")
    op.drop_table("partner_manual_products")
    op.drop_table("partner_products")
    op.drop_table("partner_match_jobs")
    op.drop_table("partner_accounts")
