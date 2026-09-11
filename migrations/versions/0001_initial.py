"""Initial DevFeed schema, generated from the current pre-release schema.

Revision ID: 0001
Revises:
App version: 0.0.1

Fresh databases only. SQL functions, triggers and standalone sequences are
frozen here alongside the generated tables, constraints and indexes.
"""

# Long SQL expressions are frozen PostgreSQL definitions.
# ruff: noqa: E501

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None
app_version: str = "0.0.1"


def upgrade() -> None:
    op.execute("CREATE SEQUENCE tag_topic_catalog_revision")
    op.execute("SELECT nextval('tag_topic_catalog_revision')")
    op.execute("CREATE SEQUENCE article_slug_number")
    op.execute("CREATE SEQUENCE topic_relationship_generation")
    # Generated table, constraint and index definitions.
    op.create_table(
        "admin_preferences",
        sa.Column("owner_key", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column(
            "settings", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.PrimaryKeyConstraint("owner_key", name="admin_preferences_pkey"),
    )
    op.create_table(
        "articles",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("canonical_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=False),
        sa.Column("url_hash", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("title", sa.VARCHAR(length=500), autoincrement=False, nullable=False),
        sa.Column("summary", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("ai_summary", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("ai_description", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column(
            "review_status",
            sa.VARCHAR(length=20),
            server_default=sa.text("'pending'::character varying"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "publication_status",
            sa.VARCHAR(length=20),
            server_default=sa.text("'unpublished'::character varying"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "published_to_feed_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "editorial_revision",
            sa.INTEGER(),
            server_default=sa.text("0"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "classification_provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("author", sa.VARCHAR(length=200), autoincrement=False, nullable=True),
        sa.Column("image_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
        sa.Column("language", sa.VARCHAR(length=35), autoincrement=False, nullable=True),
        sa.Column("content_type", sa.VARCHAR(length=30), autoincrement=False, nullable=False),
        sa.Column(
            "content_format",
            sa.VARCHAR(length=30),
            server_default=sa.text("'article'::character varying"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "published_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "feed_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "metadata_source_type", sa.VARCHAR(length=20), autoincrement=False, nullable=True
        ),
        sa.Column(
            "discovered_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "automation_started_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "automation_next_check_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("slug", sa.VARCHAR(length=200), autoincrement=False, nullable=False),
        sa.CheckConstraint(
            "metadata_source_type::text = ANY (ARRAY['publisher'::character varying, 'aggregator'::character varying, 'page'::character varying]::text[])",
            name="ck_articles_metadata_source_type",
        ),
        sa.CheckConstraint(
            "publication_status::text <> 'published'::text OR review_status::text = 'approved'::text",
            name="ck_article_published_approved",
        ),
        sa.CheckConstraint(
            "publication_status::text = ANY (ARRAY['unpublished'::character varying, 'published'::character varying]::text[])",
            name="ck_article_publication",
        ),
        sa.CheckConstraint(
            "review_status::text = ANY (ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying]::text[])",
            name="ck_article_review",
        ),
        sa.PrimaryKeyConstraint("id", name="articles_pkey"),
        sa.UniqueConstraint(
            "slug",
            name="uq_articles_slug",
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        sa.UniqueConstraint(
            "url_hash",
            name="articles_url_hash_key",
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        "ix_articles_automation_due",
        "articles",
        ["automation_next_check_at", "id"],
        unique=False,
        postgresql_where="(((review_status)::text = 'pending'::text) AND ((publication_status)::text = 'unpublished'::text))",
        postgresql_include=[],
    )
    op.create_index(
        "ix_articles_discovered_at",
        "articles",
        ["discovered_at"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_articles_feed",
        "articles",
        [sa.literal_column("feed_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_articles_published_to_feed_at",
        "articles",
        ["published_to_feed_at"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_articles_search",
        "articles",
        [
            sa.literal_column(
                "to_tsvector('english'::regconfig, (((title::text || ' '::text) || summary) || ' '::text) || COALESCE(ai_summary, ''::text))"
            )
        ],
        unique=False,
        postgresql_using="gin",
        postgresql_include=[],
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("event_key", sa.VARCHAR(length=255), autoincrement=False, nullable=False),
        sa.Column("dedup_key", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("audience", sa.VARCHAR(length=16), autoincrement=False, nullable=False),
        sa.Column("subscriber_id", sa.VARCHAR(length=128), autoincrement=False, nullable=True),
        sa.Column("category", sa.VARCHAR(length=100), autoincrement=False, nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column("status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("attempts", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "available_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "dispatched_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "lease_until", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("lease_token", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "finished_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("error", sa.VARCHAR(length=200), autoincrement=False, nullable=True),
        sa.CheckConstraint(
            "audience::text = ANY (ARRAY['admin'::character varying, 'user'::character varying]::text[])",
            name="notification_deliveries_audience_check",
        ),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying]::text[])",
            name="notification_deliveries_status_check",
        ),
        sa.CheckConstraint("attempts >= 0", name="notification_deliveries_attempts_check"),
        sa.PrimaryKeyConstraint("id", name="notification_deliveries_pkey"),
        sa.UniqueConstraint(
            "dedup_key",
            name="notification_deliveries_dedup_key_key",
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        "ix_notification_delivery_dispatch",
        "notification_deliveries",
        ["available_at"],
        unique=False,
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.create_table(
        "overview_daily",
        sa.Column("day", sa.DATE(), autoincrement=False, nullable=False),
        sa.Column(
            "metrics", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.PrimaryKeyConstraint("day", name="overview_daily_pkey"),
    )
    op.create_table(
        "recommendation_source_events",
        sa.Column("source_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "version",
            sa.BIGINT(),
            server_default=sa.text("'1'::bigint"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "pass_version",
            sa.BIGINT(),
            server_default=sa.text("'1'::bigint"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("cursor", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source_id", name="recommendation_source_events_pkey"),
    )
    op.create_table(
        "recommendation_topic_events",
        sa.Column("topic_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "version",
            sa.BIGINT(),
            server_default=sa.text("'1'::bigint"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "pass_version",
            sa.BIGINT(),
            server_default=sa.text("'1'::bigint"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("cursor", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("topic_id", name="recommendation_topic_events_pkey"),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("name", sa.VARCHAR(length=200), autoincrement=False, nullable=False),
        sa.Column("feed_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=False),
        sa.Column("source_type", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("description", sa.VARCHAR(length=500), autoincrement=False, nullable=True),
        sa.Column("website_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
        sa.Column("logo_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
        sa.Column("image_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
        sa.Column("language", sa.VARCHAR(length=35), autoincrement=False, nullable=True),
        sa.Column(
            "submitted_by",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column("submission_channel", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("approval_status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            "reviewed_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("reviewed_by", sa.VARCHAR(length=200), autoincrement=False, nullable=True),
        sa.Column("review_note", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.Column(
            "metadata_enriched_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column("metadata_error", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column("enabled", sa.BOOLEAN(), autoincrement=False, nullable=False),
        sa.Column("poll_interval_seconds", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("etag", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.Column("last_modified", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.Column(
            "next_fetch_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "last_attempt_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column(
            "last_success_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column("consecutive_failures", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("last_error", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "publication_policy",
            sa.VARCHAR(length=20),
            server_default=sa.text("'manual'::character varying"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "publication_policy_revision",
            sa.INTEGER(),
            server_default=sa.text("0"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "relevance_assessment",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "approval_status::text = ANY (ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying]::text[])",
            name="ck_sources_approval_status",
        ),
        sa.CheckConstraint(
            "publication_policy::text = ANY (ARRAY['manual'::character varying, 'preview'::character varying, 'auto'::character varying]::text[])",
            name="ck_source_publication_policy",
        ),
        sa.CheckConstraint(
            "source_type::text = ANY (ARRAY['publisher'::character varying, 'aggregator'::character varying]::text[])",
            name="ck_sources_source_type",
        ),
        sa.CheckConstraint(
            "submission_channel::text = ANY (ARRAY['api'::character varying, 'cli'::character varying, 'legacy'::character varying]::text[])",
            name="ck_sources_submission_channel",
        ),
        sa.CheckConstraint(
            "poll_interval_seconds >= 300", name="sources_poll_interval_seconds_check"
        ),
        sa.PrimaryKeyConstraint("id", name="sources_pkey"),
        sa.UniqueConstraint(
            "feed_url",
            name="sources_feed_url_key",
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        "ix_sources_approval_status",
        "sources",
        ["approval_status"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_sources_due",
        "sources",
        ["next_fetch_at"],
        unique=False,
        postgresql_where="((enabled IS TRUE) AND ((approval_status)::text = 'approved'::text))",
        postgresql_include=[],
    )
    op.create_table(
        "taxonomy_migration_archive",
        sa.Column("id", sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column("source_table", sa.VARCHAR(length=50), autoincrement=False, nullable=False),
        sa.Column(
            "record", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="taxonomy_migration_archive_pkey"),
    )
    op.create_table(
        "topics",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("name", sa.VARCHAR(length=100), autoincrement=False, nullable=False),
        sa.Column("slug", sa.VARCHAR(length=100), autoincrement=False, nullable=False),
        sa.Column("kind", sa.VARCHAR(length=50), autoincrement=False, nullable=False),
        sa.Column(
            "aliases", postgresql.ARRAY(sa.VARCHAR(length=100)), autoincrement=False, nullable=False
        ),
        sa.Column("status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("description", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("ai_description", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("website_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
        sa.Column("logo_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
        sa.Column(
            "facts", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "keywords",
            postgresql.ARRAY(sa.VARCHAR(length=100)),
            server_default=sa.text("'{}'::character varying[]"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "identity_keys",
            postgresql.ARRAY(sa.TEXT()),
            server_default=sa.text("'{}'::text[]"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['proposed'::character varying, 'active'::character varying, 'rejected'::character varying]::text[])",
            name="ck_topic_status",
        ),
        sa.PrimaryKeyConstraint("id", name="topics_pkey"),
        sa.UniqueConstraint(
            "slug",
            name="topics_slug_key",
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        "ix_topics_identity_keys",
        "topics",
        ["identity_keys"],
        unique=False,
        postgresql_using="gin",
        postgresql_include=[],
    )
    op.create_table(
        "user_accounts",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("issuer", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("subject", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("organization_id", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("name", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("email", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "last_seen_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "profile",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "notification_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "feed_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "appearance_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="user_accounts_pkey"),
        sa.UniqueConstraint(
            "issuer",
            "subject",
            name="uq_user_identity",
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        "ix_user_accounts_created_at",
        "user_accounts",
        ["created_at"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "article_analysis_jobs",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("attempts", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "available_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "dispatched_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "lease_until", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("lease_token", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "finished_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("input_hash", sa.VARCHAR(length=64), autoincrement=False, nullable=True),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "catalog_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("editorial_revision", sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column("model", sa.VARCHAR(length=100), autoincrement=False, nullable=True),
        sa.Column("prompt_version", sa.VARCHAR(length=50), autoincrement=False, nullable=True),
        sa.Column(
            "result", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column("outcome", sa.VARCHAR(length=30), autoincrement=False, nullable=True),
        sa.Column("error", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.Column("catalog_hash", sa.VARCHAR(length=64), autoincrement=False, nullable=True),
        sa.Column(
            "usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "duration_ms",
            sa.INTEGER(),
            server_default=sa.text("0"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying]::text[])",
            name="ck_analysis_status",
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="article_analysis_jobs_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="article_analysis_jobs_pkey"),
    )
    op.create_index(
        "ix_article_analysis_dispatch",
        "article_analysis_jobs",
        ["available_at"],
        unique=False,
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.create_index(
        "ix_article_analysis_jobs_article_id",
        "article_analysis_jobs",
        ["article_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "uq_article_analysis_active",
        "article_analysis_jobs",
        ["article_id"],
        unique=True,
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.create_table(
        "article_contents",
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("url", sa.VARCHAR(length=2048), autoincrement=False, nullable=False),
        sa.Column("text", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("content_hash", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("method", sa.VARCHAR(length=30), autoincrement=False, nullable=False),
        sa.Column(
            "retrieved_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="article_contents_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("article_id", name="article_contents_pkey"),
    )
    op.create_table(
        "article_engagement",
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("opens", sa.BIGINT(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="article_engagement_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("article_id", name="article_engagement_pkey"),
    )
    op.create_table(
        "article_enrichment_jobs",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("attempts", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "available_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "dispatched_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "lease_until", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("lease_token", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "finished_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("http_status", sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column("outcome", sa.VARCHAR(length=20), autoincrement=False, nullable=True),
        sa.Column(
            "changed_fields",
            postgresql.ARRAY(sa.VARCHAR(length=30)),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "result", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column("error", sa.TEXT(), autoincrement=False, nullable=True),
        sa.CheckConstraint(
            "outcome::text = ANY (ARRAY['enriched'::character varying, 'metadata_only'::character varying, 'not_found'::character varying, 'superseded'::character varying, 'unapproved'::character varying]::text[])",
            name="article_enrichment_jobs_outcome_check",
        ),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying]::text[])",
            name="article_enrichment_jobs_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="article_enrichment_jobs_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="article_enrichment_jobs_pkey"),
    )
    op.create_index(
        "ix_article_enrichment_dispatch",
        "article_enrichment_jobs",
        ["available_at"],
        unique=False,
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.create_index(
        "ix_article_enrichment_jobs_article_id",
        "article_enrichment_jobs",
        ["article_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "uq_article_enrichment_active",
        "article_enrichment_jobs",
        ["article_id"],
        unique=True,
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.create_table(
        "article_image_jobs",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("attempts", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "available_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "dispatched_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "lease_until", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("lease_token", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "finished_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("http_status", sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column("outcome", sa.VARCHAR(length=20), autoincrement=False, nullable=True),
        sa.Column("image_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
        sa.Column("method", sa.VARCHAR(length=30), autoincrement=False, nullable=True),
        sa.Column("error", sa.TEXT(), autoincrement=False, nullable=True),
        sa.CheckConstraint(
            "outcome::text = ANY (ARRAY['found'::character varying, 'not_found'::character varying, 'already_present'::character varying]::text[])",
            name="article_image_jobs_outcome_check",
        ),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying]::text[])",
            name="article_image_jobs_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="article_image_jobs_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="article_image_jobs_pkey"),
    )
    op.create_index(
        "ix_article_image_dispatch",
        "article_image_jobs",
        ["available_at"],
        unique=False,
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.create_index(
        "ix_article_image_jobs_article_id",
        "article_image_jobs",
        ["article_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "uq_article_image_active",
        "article_image_jobs",
        ["article_id"],
        unique=True,
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.create_table(
        "article_likes",
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("user_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="article_likes_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_accounts.id"], name="article_likes_user_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("article_id", "user_id", name="article_likes_pkey"),
    )
    op.create_index(
        "ix_article_likes_created_at",
        "article_likes",
        ["created_at"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_article_likes_user_id",
        "article_likes",
        ["user_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_article_likes_user_recent",
        "article_likes",
        ["user_id", sa.literal_column("created_at DESC"), "article_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "article_opens",
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("viewer_key", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column(
            "opened_hour", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="article_opens_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "article_id", "viewer_key", "opened_hour", name="article_opens_pkey"
        ),
    )
    op.create_index(
        "ix_article_opens_opened_hour",
        "article_opens",
        ["opened_hour"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "article_origins",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("source_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("entry_key", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("original_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=False),
        sa.Column(
            "source_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="article_origins_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="article_origins_source_id_fkey"
        ),
        sa.PrimaryKeyConstraint("id", name="article_origins_pkey"),
        sa.UniqueConstraint(
            "source_id",
            "entry_key",
            name="article_origins_source_id_entry_key_key",
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        "ix_article_origins_article_id",
        "article_origins",
        ["article_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_article_origins_source_id",
        "article_origins",
        ["source_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "article_publication_decisions",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("fingerprint", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column(
            "decision", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="article_publication_decisions_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="article_publication_decisions_pkey"),
        sa.UniqueConstraint(
            "fingerprint",
            name="article_publication_decisions_fingerprint_key",
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        "ix_article_publication_decisions_article_id",
        "article_publication_decisions",
        ["article_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "article_reviews",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("action", sa.VARCHAR(length=30), autoincrement=False, nullable=False),
        sa.Column("actor", sa.VARCHAR(length=200), autoincrement=False, nullable=True),
        sa.Column("note", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.Column("revision", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "automation",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="article_reviews_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="article_reviews_pkey"),
    )
    op.create_index(
        "ix_article_reviews_article_id",
        "article_reviews",
        ["article_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "article_topics",
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("topic_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("role", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            "relevance", sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=False
        ),
        sa.Column("evidence", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column("origin", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.CheckConstraint(
            "role::text = ANY (ARRAY['primary'::character varying, 'supporting'::character varying, 'comparison'::character varying, 'incidental'::character varying]::text[])",
            name="ck_article_topic_role",
        ),
        sa.CheckConstraint(
            "relevance >= 0::double precision AND relevance <= 1::double precision",
            name="ck_article_topic_relevance",
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="article_topics_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topics.id"], name="article_topics_topic_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("article_id", "topic_id", name="article_topics_pkey"),
    )
    op.create_index(
        "ix_article_topics_topic",
        "article_topics",
        ["topic_id", "article_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "feed_notification_events",
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("topic_ids", postgresql.ARRAY(sa.UUID()), autoincrement=False, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column("recipient_cursor", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "completed_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "source_ids",
            postgresql.ARRAY(sa.UUID()),
            server_default=sa.text("'{}'::uuid[]"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="feed_notification_events_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("article_id", name="feed_notification_events_pkey"),
    )
    op.create_index(
        "ix_feed_notification_pending",
        "feed_notification_events",
        ["created_at"],
        unique=False,
        postgresql_where="(completed_at IS NULL)",
        postgresql_include=[],
    )
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("source_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("attempts", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "available_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "dispatched_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "lease_until", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("lease_token", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "finished_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("http_status", sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column("entries_seen", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("articles_created", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("entries_skipped", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("error", sa.TEXT(), autoincrement=False, nullable=True),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying]::text[])",
            name="ingestion_jobs_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="ingestion_jobs_source_id_fkey"
        ),
        sa.PrimaryKeyConstraint("id", name="ingestion_jobs_pkey"),
    )
    op.create_index(
        "ix_ingestion_dispatch",
        "ingestion_jobs",
        ["available_at"],
        unique=False,
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.create_index(
        "ix_ingestion_jobs_source_id",
        "ingestion_jobs",
        ["source_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "uq_ingestion_active_source",
        "ingestion_jobs",
        ["source_id"],
        unique=True,
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.create_table(
        "source_enrichment_jobs",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("source_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("attempts", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "available_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "dispatched_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "lease_until", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("lease_token", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "finished_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("error", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.Column(
            "changed_fields",
            postgresql.ARRAY(sa.VARCHAR(length=30)),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying]::text[])",
            name="source_enrichment_jobs_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="source_enrichment_jobs_source_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="source_enrichment_jobs_pkey"),
    )
    op.create_index(
        "ix_source_enrichment_dispatch",
        "source_enrichment_jobs",
        ["available_at"],
        unique=False,
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.create_index(
        "ix_source_enrichment_jobs_source_id",
        "source_enrichment_jobs",
        ["source_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "uq_source_enrichment_active",
        "source_enrichment_jobs",
        ["source_id"],
        unique=True,
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.create_table(
        "source_publication_policy_reviews",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("source_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("mode", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("revision", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("actor", sa.VARCHAR(length=200), autoincrement=False, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="source_publication_policy_reviews_source_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="source_publication_policy_reviews_pkey"),
    )
    op.create_index(
        "ix_source_publication_policy_reviews_source_id",
        "source_publication_policy_reviews",
        ["source_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "source_reviews",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("source_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("decision", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("actor", sa.VARCHAR(length=200), autoincrement=False, nullable=True),
        sa.Column("note", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.CheckConstraint(
            "decision::text = ANY (ARRAY['approved'::character varying, 'rejected'::character varying]::text[])",
            name="source_reviews_decision_check",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="source_reviews_source_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="source_reviews_pkey"),
    )
    op.create_index(
        "ix_source_reviews_source_id",
        "source_reviews",
        ["source_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "tags",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("name", sa.VARCHAR(length=100), autoincrement=False, nullable=False),
        sa.Column("slug", sa.VARCHAR(length=100), autoincrement=False, nullable=False),
        sa.Column(
            "aliases", postgresql.ARRAY(sa.VARCHAR(length=100)), autoincrement=False, nullable=False
        ),
        sa.Column("topic_id", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "auto_link_topic",
            sa.BOOLEAN(),
            server_default=sa.text("true"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "topic_match_revision",
            sa.BIGINT(),
            server_default=sa.text("'0'::bigint"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "topic_match_status",
            sa.VARCHAR(length=20),
            server_default=sa.text("'pending'::character varying"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "topic_match_checked_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], name="tags_topic_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="tags_pkey"),
        sa.UniqueConstraint(
            "slug", name="tags_slug_key", postgresql_include=[], postgresql_nulls_not_distinct=False
        ),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        "ix_tags_topic_discovery",
        "tags",
        ["topic_match_revision", "id"],
        unique=False,
        postgresql_where="(auto_link_topic IS TRUE)",
        postgresql_include=[],
    )
    op.create_table(
        "topic_proposals",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("batch_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("topic_id", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column("slug", sa.VARCHAR(length=100), autoincrement=False, nullable=False),
        sa.Column("action", sa.VARCHAR(length=10), autoincrement=False, nullable=False),
        sa.Column("origin", sa.VARCHAR(length=30), autoincrement=False, nullable=False),
        sa.Column("source_name", sa.VARCHAR(length=200), autoincrement=False, nullable=False),
        sa.Column(
            "proposed", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column(
            "baseline", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True
        ),
        sa.Column(
            "evidence", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column("status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "created_by",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "reviewed_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "reviewed_by",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column("review_note", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.Column(
            "applied", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True
        ),
        sa.Column(
            "research_requested",
            sa.BOOLEAN(),
            server_default=sa.text("false"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "action::text = ANY (ARRAY['create'::character varying, 'update'::character varying]::text[])",
            name="topic_proposals_action_check",
        ),
        sa.CheckConstraint(
            "origin::text = ANY (ARRAY['import'::character varying, 'article_enrichment'::character varying, 'ai_analysis'::character varying]::text[])",
            name="topic_proposals_origin_check",
        ),
        sa.CheckConstraint(
            "status::text <> 'approved'::text OR applied IS NOT NULL", name="topic_proposals_check1"
        ),
        sa.CheckConstraint(
            "status::text = 'pending'::text OR reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL",
            name="topic_proposals_check",
        ),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying]::text[])",
            name="topic_proposals_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topics.id"], name="topic_proposals_topic_id_fkey", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="topic_proposals_pkey"),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        "ix_topic_proposals_batch_id",
        "topic_proposals",
        ["batch_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_topic_proposals_status",
        "topic_proposals",
        ["status"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_topic_proposals_topic_id",
        "topic_proposals",
        ["topic_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "uq_topic_proposal_pending_slug",
        "topic_proposals",
        ["slug"],
        unique=True,
        postgresql_where="((status)::text = 'pending'::text)",
        postgresql_include=[],
    )
    op.create_index(
        "uq_topic_proposal_pending_target",
        "topic_proposals",
        ["topic_id"],
        unique=True,
        postgresql_where="((status)::text = 'pending'::text)",
        postgresql_include=[],
    )
    op.create_table(
        "topic_reanalysis",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("topic_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "topic_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("after_article_id", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "finished_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topics.id"], name="topic_reanalysis_topic_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="topic_reanalysis_pkey"),
    )
    op.create_index(
        "ix_topic_reanalysis_pending",
        "topic_reanalysis",
        ["created_at"],
        unique=False,
        postgresql_where="(finished_at IS NULL)",
        postgresql_include=[],
    )
    op.create_table(
        "topic_relations",
        sa.Column("topic_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("related_topic_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("relation", sa.VARCHAR(length=50), autoincrement=False, nullable=False),
        sa.Column("evidence_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
        sa.CheckConstraint("topic_id <> related_topic_id", name="ck_topic_relation_not_self"),
        sa.ForeignKeyConstraint(
            ["related_topic_id"],
            ["topics.id"],
            name="topic_relations_related_topic_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topics.id"], name="topic_relations_topic_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint(
            "topic_id", "related_topic_id", "relation", name="topic_relations_pkey"
        ),
    )
    op.create_table(
        "user_interests",
        sa.Column("user_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("topic_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("seed_topic_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("weight", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("reason", sa.VARCHAR(length=30), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_accounts.id"],
            name="user_interests_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "topic_id", name="user_interests_pkey"),
    )
    op.create_index(
        "ix_user_interests_topic_user",
        "user_interests",
        ["topic_id", "user_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "user_recommendation_states",
        sa.Column("user_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("generation", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "invalidated",
            sa.BOOLEAN(),
            server_default=sa.text("true"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "next_refresh_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "computed_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "expires_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "dispatched_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "attempts",
            sa.INTEGER(),
            server_default=sa.text("0"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "interest_count",
            sa.INTEGER(),
            server_default=sa.text("0"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_accounts.id"],
            name="user_recommendation_states_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="user_recommendation_states_pkey"),
    )
    op.create_index(
        "ix_user_recommendations_due",
        "user_recommendation_states",
        ["next_refresh_at"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "user_recommendations",
        sa.Column("user_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("position", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column("score", sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=False),
        sa.Column("topic_id", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column("seed_topic_id", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column("reason", sa.VARCHAR(length=30), autoincrement=False, nullable=False),
        sa.Column("source_id", sa.UUID(), autoincrement=False, nullable=True),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["articles.id"],
            name="user_recommendations_article_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_accounts.id"],
            name="user_recommendations_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "article_id", name="user_recommendations_pkey"),
        sa.UniqueConstraint(
            "user_id",
            "position",
            name="user_recommendations_user_id_position_key",
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_table(
        "user_sources",
        sa.Column("user_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("source_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="user_sources_source_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_accounts.id"], name="user_sources_user_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "source_id", name="user_sources_pkey"),
    )
    op.create_index(
        "ix_user_sources_source_user",
        "user_sources",
        ["source_id", "user_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "user_topics",
        sa.Column("user_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("topic_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topics.id"], name="user_topics_topic_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user_accounts.id"], name="user_topics_user_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "topic_id", name="user_topics_pkey"),
    )
    op.create_index(
        "ix_user_topics_topic_id", "user_topics", ["topic_id"], unique=False, postgresql_include=[]
    )
    op.create_table(
        "article_tags",
        sa.Column(
            "origin",
            sa.VARCHAR(length=20),
            server_default=sa.text("'heuristic'::character varying"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("article_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("tag_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(
            ["article_id"], ["articles.id"], name="article_tags_article_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name="article_tags_tag_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("article_id", "tag_id", name="article_tags_pkey"),
    )
    op.create_index(
        "ix_article_tags_tag",
        "article_tags",
        ["tag_id", "article_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_table(
        "topic_analysis_jobs",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("proposal_id", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column("status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("attempts", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "available_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "dispatched_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "lease_until", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("lease_token", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "finished_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("input_hash", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("model", sa.VARCHAR(length=100), autoincrement=False, nullable=True),
        sa.Column("prompt_version", sa.VARCHAR(length=50), autoincrement=False, nullable=False),
        sa.Column(
            "result", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column("outcome", sa.VARCHAR(length=30), autoincrement=False, nullable=True),
        sa.Column("error", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.Column("topic_id", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "duration_ms",
            sa.INTEGER(),
            server_default=sa.text("0"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying]::text[])",
            name="topic_analysis_jobs_status_check",
        ),
        sa.CheckConstraint(
            "(proposal_id IS NULL) <> (topic_id IS NULL)", name="ck_topic_analysis_target"
        ),
        sa.CheckConstraint("attempts >= 0", name="topic_analysis_jobs_attempts_check"),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["topic_proposals.id"],
            name="topic_analysis_jobs_proposal_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topics.id"], name="fk_topic_analysis_topic", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="topic_analysis_jobs_pkey"),
        postgresql_ignore_search_path=False,
    )
    op.create_index(
        "ix_topic_analysis_dispatch",
        "topic_analysis_jobs",
        ["available_at"],
        unique=False,
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.create_index(
        "ix_topic_analysis_jobs_proposal_id",
        "topic_analysis_jobs",
        ["proposal_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_topic_analysis_jobs_topic_id",
        "topic_analysis_jobs",
        ["topic_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "uq_topic_analysis_active",
        "topic_analysis_jobs",
        ["proposal_id"],
        unique=True,
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.create_index(
        "uq_topic_relationship_analysis_active",
        "topic_analysis_jobs",
        ["topic_id"],
        unique=True,
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.create_table(
        "research_verification_jobs",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("relationships", sa.BOOLEAN(), autoincrement=False, nullable=False),
        sa.Column("status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column("attempts", sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column(
            "available_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "dispatched_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "lease_until", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("lease_token", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "finished_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column("outcome", sa.VARCHAR(length=30), autoincrement=False, nullable=True),
        sa.Column("error", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.Column(
            "usage",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "duration_ms",
            sa.INTEGER(),
            server_default=sa.text("0"),
            autoincrement=False,
            nullable=False,
        ),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['queued'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying]::text[])",
            name="research_verification_jobs_status_check",
        ),
        sa.CheckConstraint("attempts >= 0", name="research_verification_jobs_attempts_check"),
        sa.ForeignKeyConstraint(
            ["id"],
            ["topic_analysis_jobs.id"],
            name="research_verification_jobs_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="research_verification_jobs_pkey"),
    )
    op.create_index(
        "ix_research_verification_dispatch",
        "research_verification_jobs",
        ["available_at"],
        unique=False,
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.create_table(
        "topic_relation_proposals",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("job_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("topic_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("related_topic_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("relation", sa.VARCHAR(length=50), autoincrement=False, nullable=False),
        sa.Column("explanation", sa.VARCHAR(length=1000), autoincrement=False, nullable=False),
        sa.Column("evidence_url", sa.VARCHAR(length=2048), autoincrement=False, nullable=False),
        sa.Column("evidence_title", sa.VARCHAR(length=300), autoincrement=False, nullable=False),
        sa.Column("evidence_quote", sa.VARCHAR(length=500), autoincrement=False, nullable=False),
        sa.Column(
            "topic_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "related_topic_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("status", sa.VARCHAR(length=20), autoincrement=False, nullable=False),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "created_by",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "reviewed_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "reviewed_by",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=True,
        ),
        sa.Column("review_note", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.CheckConstraint(
            "status::text = 'pending'::text OR reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL",
            name="topic_relation_proposals_check1",
        ),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying]::text[])",
            name="topic_relation_proposals_status_check",
        ),
        sa.CheckConstraint("topic_id <> related_topic_id", name="topic_relation_proposals_check"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["topic_analysis_jobs.id"],
            name="topic_relation_proposals_job_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["related_topic_id"],
            ["topics.id"],
            name="topic_relation_proposals_related_topic_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name="topic_relation_proposals_topic_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="topic_relation_proposals_pkey"),
    )
    op.create_index(
        "ix_topic_relation_proposals_job_id",
        "topic_relation_proposals",
        ["job_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_topic_relation_proposals_related_topic_id",
        "topic_relation_proposals",
        ["related_topic_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_topic_relation_proposals_status",
        "topic_relation_proposals",
        ["status"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_topic_relation_proposals_topic_id",
        "topic_relation_proposals",
        ["topic_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "uq_relation_proposal_pending",
        "topic_relation_proposals",
        ["topic_id", "related_topic_id", "relation"],
        unique=True,
        postgresql_where="((status)::text = 'pending'::text)",
        postgresql_include=[],
    )
    op.create_table(
        "topic_relationship_scans",
        sa.Column("topic_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "generation",
            sa.BIGINT(),
            server_default=sa.text("nextval('topic_relationship_generation'::regclass)"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "topic_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("after_topic_id", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column("job_id", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column(
            "next_run_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False
        ),
        sa.Column(
            "finished_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
        sa.Column(
            "failures",
            sa.INTEGER(),
            server_default=sa.text("0"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("last_error", sa.VARCHAR(length=1000), autoincrement=False, nullable=True),
        sa.CheckConstraint("failures >= 0", name="topic_relationship_scans_failures_check"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["topic_analysis_jobs.id"],
            name="topic_relationship_scans_job_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name="topic_relationship_scans_topic_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("topic_id", name="topic_relationship_scans_pkey"),
    )
    op.create_index(
        "ix_topic_relationship_scans_generation",
        "topic_relationship_scans",
        ["generation"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_topic_relationship_scans_job_id",
        "topic_relationship_scans",
        ["job_id"],
        unique=False,
        postgresql_include=[],
    )
    op.create_index(
        "ix_topic_relationship_scans_pending",
        "topic_relationship_scans",
        ["next_run_at"],
        unique=False,
        postgresql_where="(finished_at IS NULL)",
        postgresql_include=[],
    )

    op.execute(r"""
CREATE OR REPLACE FUNCTION public.assign_article_slug()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        BEGIN
            NEW.slug := new_article_slug(NEW.title);
            RETURN NEW;
        END $function$

""")
    op.execute(r"""
CREATE OR REPLACE FUNCTION public.new_article_slug(article_title text)
 RETURNS text
 LANGUAGE sql
AS $function$
            SELECT coalesce(nullif(trim(both '-' from left(
                regexp_replace(
                    regexp_replace(lower(normalize(article_title, NFKD)),
                                   U&'[\0300-\036f]', '', 'g'),
                    '[^a-z0-9]+', '-', 'g'), 170)), ''), 'article')
                || '-' || nextval('article_slug_number')::text
        $function$

""")
    op.execute(r"""
CREATE OR REPLACE FUNCTION public.recommendation_catalog_change()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        DECLARE item record;
        BEGIN
          IF TG_TABLE_NAME = 'article_topics' THEN
            IF TG_OP <> 'INSERT' THEN PERFORM recommendation_touch_topic(OLD.topic_id); END IF;
            IF TG_OP <> 'DELETE' THEN PERFORM recommendation_touch_topic(NEW.topic_id); END IF;
          ELSIF TG_TABLE_NAME = 'topic_relations' THEN
            IF TG_OP <> 'INSERT' THEN
              PERFORM recommendation_touch_topic(OLD.topic_id);
              PERFORM recommendation_touch_topic(OLD.related_topic_id);
            END IF;
            IF TG_OP <> 'DELETE' THEN
              PERFORM recommendation_touch_topic(NEW.topic_id);
              PERFORM recommendation_touch_topic(NEW.related_topic_id);
            END IF;
          ELSIF TG_TABLE_NAME = 'topics' THEN
            IF TG_OP = 'DELETE' THEN PERFORM recommendation_touch_topic(OLD.id);
            ELSE PERFORM recommendation_touch_topic(NEW.id); END IF;
          ELSIF TG_TABLE_NAME = 'articles' THEN
            PERFORM recommendation_touch_article(NEW.id);
          ELSIF TG_TABLE_NAME = 'article_origins' THEN
            IF TG_OP <> 'INSERT' THEN PERFORM recommendation_touch_article(OLD.article_id); END IF;
            IF TG_OP <> 'DELETE' THEN PERFORM recommendation_touch_article(NEW.article_id); END IF;
          ELSIF TG_TABLE_NAME = 'sources' THEN
            FOR item IN SELECT DISTINCT at.topic_id FROM article_origins ao
              JOIN article_topics at ON at.article_id = ao.article_id
              WHERE ao.source_id = NEW.id ORDER BY at.topic_id
            LOOP PERFORM recommendation_touch_topic(item.topic_id); END LOOP;
          END IF;
          RETURN NULL;
        END $function$

""")
    op.execute(r"""
CREATE OR REPLACE FUNCTION public.recommendation_request_user()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        DECLARE target uuid;
        BEGIN
          IF TG_TABLE_NAME = 'user_accounts' THEN target := NEW.id;
          ELSIF TG_OP = 'DELETE' THEN target := OLD.user_id;
          ELSE target := NEW.user_id; END IF;
          INSERT INTO user_recommendation_states(user_id)
            SELECT id FROM user_accounts WHERE id = target
          ON CONFLICT (user_id) DO UPDATE SET invalidated = true,
            next_refresh_at = now(), dispatched_at = NULL, attempts = 0;
          RETURN NULL;
        END $function$

""")
    op.execute(r"""
CREATE OR REPLACE FUNCTION public.recommendation_source_change()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
      DECLARE item record;
      BEGIN
        IF TG_TABLE_NAME = 'sources' THEN
          PERFORM recommendation_touch_source(NEW.id);
        ELSIF TG_TABLE_NAME = 'article_origins' THEN
          IF TG_OP <> 'INSERT' THEN PERFORM recommendation_touch_source(OLD.source_id); END IF;
          IF TG_OP <> 'DELETE' THEN PERFORM recommendation_touch_source(NEW.source_id); END IF;
        ELSE
          FOR item IN SELECT DISTINCT source_id FROM article_origins
            WHERE article_id = NEW.id ORDER BY source_id
          LOOP PERFORM recommendation_touch_source(item.source_id); END LOOP;
        END IF;
        RETURN NULL;
      END $function$

""")
    op.execute(r"""
CREATE OR REPLACE FUNCTION public.recommendation_touch_article(target uuid)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
        DECLARE item record;
        BEGIN
          FOR item IN SELECT topic_id FROM article_topics
            WHERE article_id = target ORDER BY topic_id
          LOOP PERFORM recommendation_touch_topic(item.topic_id); END LOOP;
        END $function$

""")
    op.execute(r"""
CREATE OR REPLACE FUNCTION public.recommendation_touch_source(target uuid)
 RETURNS void
 LANGUAGE sql
AS $function$
        INSERT INTO recommendation_source_events(source_id) VALUES(target)
        ON CONFLICT (source_id) DO UPDATE SET version = recommendation_source_events.version + 1;
      $function$

""")
    op.execute(r"""
CREATE OR REPLACE FUNCTION public.recommendation_touch_topic(target uuid)
 RETURNS void
 LANGUAGE sql
AS $function$
          INSERT INTO recommendation_topic_events(topic_id) VALUES(target)
          ON CONFLICT (topic_id) DO UPDATE SET version = recommendation_topic_events.version + 1;
        $function$

""")
    op.execute(
        "CREATE TRIGGER recommendation_likes AFTER INSERT OR DELETE OR UPDATE ON public.article_likes FOR EACH ROW EXECUTE FUNCTION recommendation_request_user()"
    )
    op.execute(
        "CREATE TRIGGER recommendation_origins AFTER INSERT OR DELETE OR UPDATE ON public.article_origins FOR EACH ROW EXECUTE FUNCTION recommendation_catalog_change()"
    )
    op.execute(
        "CREATE TRIGGER recommendation_source_origins AFTER INSERT OR DELETE OR UPDATE ON public.article_origins FOR EACH ROW EXECUTE FUNCTION recommendation_source_change()"
    )
    op.execute(
        "CREATE TRIGGER recommendation_article_topics AFTER INSERT OR DELETE OR UPDATE ON public.article_topics FOR EACH ROW EXECUTE FUNCTION recommendation_catalog_change()"
    )
    op.execute(
        "CREATE TRIGGER articles_assign_slug BEFORE INSERT ON public.articles FOR EACH ROW EXECUTE FUNCTION assign_article_slug()"
    )
    op.execute(
        "CREATE TRIGGER recommendation_articles AFTER UPDATE OF publication_status, review_status, feed_at, content_type ON public.articles FOR EACH ROW WHEN ((((((old.publication_status)::text IS DISTINCT FROM (new.publication_status)::text) OR ((old.review_status)::text IS DISTINCT FROM (new.review_status)::text)) OR (old.feed_at IS DISTINCT FROM new.feed_at)) OR ((old.content_type)::text IS DISTINCT FROM (new.content_type)::text))) EXECUTE FUNCTION recommendation_catalog_change()"
    )
    op.execute(
        "CREATE TRIGGER recommendation_source_articles AFTER UPDATE OF publication_status, review_status, feed_at, content_type ON public.articles FOR EACH ROW WHEN ((((((old.publication_status)::text IS DISTINCT FROM (new.publication_status)::text) OR ((old.review_status)::text IS DISTINCT FROM (new.review_status)::text)) OR (old.feed_at IS DISTINCT FROM new.feed_at)) OR ((old.content_type)::text IS DISTINCT FROM (new.content_type)::text))) EXECUTE FUNCTION recommendation_source_change()"
    )
    op.execute(
        "CREATE TRIGGER recommendation_source_approval AFTER UPDATE OF approval_status ON public.sources FOR EACH ROW WHEN (((old.approval_status)::text IS DISTINCT FROM (new.approval_status)::text)) EXECUTE FUNCTION recommendation_source_change()"
    )
    op.execute(
        "CREATE TRIGGER recommendation_sources AFTER UPDATE OF approval_status ON public.sources FOR EACH ROW WHEN (((old.approval_status)::text IS DISTINCT FROM (new.approval_status)::text)) EXECUTE FUNCTION recommendation_catalog_change()"
    )
    op.execute(
        "CREATE TRIGGER recommendation_relations AFTER INSERT OR DELETE OR UPDATE ON public.topic_relations FOR EACH ROW EXECUTE FUNCTION recommendation_catalog_change()"
    )
    op.execute(
        "CREATE TRIGGER recommendation_topics AFTER DELETE OR UPDATE OF status, name ON public.topics FOR EACH ROW EXECUTE FUNCTION recommendation_catalog_change()"
    )
    op.execute(
        "CREATE TRIGGER recommendation_account AFTER INSERT ON public.user_accounts FOR EACH ROW EXECUTE FUNCTION recommendation_request_user()"
    )
    op.execute(
        'CREATE TRIGGER recommendation_content_preferences AFTER UPDATE OF feed_settings ON public.user_accounts FOR EACH ROW WHEN ((COALESCE((old.feed_settings -> \'content_types\'::text), \'["article", "news", "tutorial", "release", "comparison", "opinion"]\'::jsonb) IS DISTINCT FROM COALESCE((new.feed_settings -> \'content_types\'::text), \'["article", "news", "tutorial", "release", "comparison", "opinion"]\'::jsonb))) EXECUTE FUNCTION recommendation_request_user()'
    )
    op.execute(
        "CREATE TRIGGER recommendation_source_follows AFTER INSERT OR DELETE OR UPDATE ON public.user_sources FOR EACH ROW EXECUTE FUNCTION recommendation_request_user()"
    )
    op.execute(
        "CREATE TRIGGER recommendation_follows AFTER INSERT OR DELETE OR UPDATE ON public.user_topics FOR EACH ROW EXECUTE FUNCTION recommendation_request_user()"
    )
    op.execute("ALTER TABLE article_topics ALTER COLUMN topic_id SET STATISTICS 1000")
    op.execute("ALTER TABLE article_origins ALTER COLUMN source_id SET STATISTICS 1000")


def downgrade() -> None:
    op.execute("DROP TRIGGER recommendation_likes ON article_likes")
    op.execute("DROP TRIGGER recommendation_origins ON article_origins")
    op.execute("DROP TRIGGER recommendation_source_origins ON article_origins")
    op.execute("DROP TRIGGER recommendation_article_topics ON article_topics")
    op.execute("DROP TRIGGER articles_assign_slug ON articles")
    op.execute("DROP TRIGGER recommendation_articles ON articles")
    op.execute("DROP TRIGGER recommendation_source_articles ON articles")
    op.execute("DROP TRIGGER recommendation_source_approval ON sources")
    op.execute("DROP TRIGGER recommendation_sources ON sources")
    op.execute("DROP TRIGGER recommendation_relations ON topic_relations")
    op.execute("DROP TRIGGER recommendation_topics ON topics")
    op.execute("DROP TRIGGER recommendation_account ON user_accounts")
    op.execute("DROP TRIGGER recommendation_content_preferences ON user_accounts")
    op.execute("DROP TRIGGER recommendation_source_follows ON user_sources")
    op.execute("DROP TRIGGER recommendation_follows ON user_topics")
    op.execute(
        "DROP FUNCTION assign_article_slug(), new_article_slug(article_title text), recommendation_catalog_change(), recommendation_request_user(), recommendation_source_change(), recommendation_touch_article(target uuid), recommendation_touch_source(target uuid), recommendation_touch_topic(target uuid)"
    )
    # Generated table, constraint and index definitions.
    op.drop_index(
        "ix_topic_relationship_scans_pending",
        table_name="topic_relationship_scans",
        postgresql_where="(finished_at IS NULL)",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_topic_relationship_scans_job_id",
        table_name="topic_relationship_scans",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_topic_relationship_scans_generation",
        table_name="topic_relationship_scans",
        postgresql_include=[],
    )
    op.drop_table("topic_relationship_scans")
    op.drop_index(
        "uq_relation_proposal_pending",
        table_name="topic_relation_proposals",
        postgresql_where="((status)::text = 'pending'::text)",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_topic_relation_proposals_topic_id",
        table_name="topic_relation_proposals",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_topic_relation_proposals_status",
        table_name="topic_relation_proposals",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_topic_relation_proposals_related_topic_id",
        table_name="topic_relation_proposals",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_topic_relation_proposals_job_id",
        table_name="topic_relation_proposals",
        postgresql_include=[],
    )
    op.drop_table("topic_relation_proposals")
    op.drop_index(
        "ix_research_verification_dispatch",
        table_name="research_verification_jobs",
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.drop_table("research_verification_jobs")
    op.drop_index(
        "uq_topic_relationship_analysis_active",
        table_name="topic_analysis_jobs",
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.drop_index(
        "uq_topic_analysis_active",
        table_name="topic_analysis_jobs",
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_topic_analysis_jobs_topic_id", table_name="topic_analysis_jobs", postgresql_include=[]
    )
    op.drop_index(
        "ix_topic_analysis_jobs_proposal_id",
        table_name="topic_analysis_jobs",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_topic_analysis_dispatch",
        table_name="topic_analysis_jobs",
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.drop_table("topic_analysis_jobs")
    op.drop_index("ix_article_tags_tag", table_name="article_tags", postgresql_include=[])
    op.drop_table("article_tags")
    op.drop_index("ix_user_topics_topic_id", table_name="user_topics", postgresql_include=[])
    op.drop_table("user_topics")
    op.drop_index("ix_user_sources_source_user", table_name="user_sources", postgresql_include=[])
    op.drop_table("user_sources")
    op.drop_table("user_recommendations")
    op.drop_index(
        "ix_user_recommendations_due",
        table_name="user_recommendation_states",
        postgresql_include=[],
    )
    op.drop_table("user_recommendation_states")
    op.drop_index(
        "ix_user_interests_topic_user", table_name="user_interests", postgresql_include=[]
    )
    op.drop_table("user_interests")
    op.drop_table("topic_relations")
    op.drop_index(
        "ix_topic_reanalysis_pending",
        table_name="topic_reanalysis",
        postgresql_where="(finished_at IS NULL)",
        postgresql_include=[],
    )
    op.drop_table("topic_reanalysis")
    op.drop_index(
        "uq_topic_proposal_pending_target",
        table_name="topic_proposals",
        postgresql_where="((status)::text = 'pending'::text)",
        postgresql_include=[],
    )
    op.drop_index(
        "uq_topic_proposal_pending_slug",
        table_name="topic_proposals",
        postgresql_where="((status)::text = 'pending'::text)",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_topic_proposals_topic_id", table_name="topic_proposals", postgresql_include=[]
    )
    op.drop_index("ix_topic_proposals_status", table_name="topic_proposals", postgresql_include=[])
    op.drop_index(
        "ix_topic_proposals_batch_id", table_name="topic_proposals", postgresql_include=[]
    )
    op.drop_table("topic_proposals")
    op.drop_index(
        "ix_tags_topic_discovery",
        table_name="tags",
        postgresql_where="(auto_link_topic IS TRUE)",
        postgresql_include=[],
    )
    op.drop_table("tags")
    op.drop_index("ix_source_reviews_source_id", table_name="source_reviews", postgresql_include=[])
    op.drop_table("source_reviews")
    op.drop_index(
        "ix_source_publication_policy_reviews_source_id",
        table_name="source_publication_policy_reviews",
        postgresql_include=[],
    )
    op.drop_table("source_publication_policy_reviews")
    op.drop_index(
        "uq_source_enrichment_active",
        table_name="source_enrichment_jobs",
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_source_enrichment_jobs_source_id",
        table_name="source_enrichment_jobs",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_source_enrichment_dispatch",
        table_name="source_enrichment_jobs",
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.drop_table("source_enrichment_jobs")
    op.drop_index(
        "uq_ingestion_active_source",
        table_name="ingestion_jobs",
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.drop_index("ix_ingestion_jobs_source_id", table_name="ingestion_jobs", postgresql_include=[])
    op.drop_index(
        "ix_ingestion_dispatch",
        table_name="ingestion_jobs",
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.drop_table("ingestion_jobs")
    op.drop_index(
        "ix_feed_notification_pending",
        table_name="feed_notification_events",
        postgresql_where="(completed_at IS NULL)",
        postgresql_include=[],
    )
    op.drop_table("feed_notification_events")
    op.drop_index("ix_article_topics_topic", table_name="article_topics", postgresql_include=[])
    op.drop_table("article_topics")
    op.drop_index(
        "ix_article_reviews_article_id", table_name="article_reviews", postgresql_include=[]
    )
    op.drop_table("article_reviews")
    op.drop_index(
        "ix_article_publication_decisions_article_id",
        table_name="article_publication_decisions",
        postgresql_include=[],
    )
    op.drop_table("article_publication_decisions")
    op.drop_index(
        "ix_article_origins_source_id", table_name="article_origins", postgresql_include=[]
    )
    op.drop_index(
        "ix_article_origins_article_id", table_name="article_origins", postgresql_include=[]
    )
    op.drop_table("article_origins")
    op.drop_index("ix_article_opens_opened_hour", table_name="article_opens", postgresql_include=[])
    op.drop_table("article_opens")
    op.drop_index("ix_article_likes_user_recent", table_name="article_likes", postgresql_include=[])
    op.drop_index("ix_article_likes_user_id", table_name="article_likes", postgresql_include=[])
    op.drop_index("ix_article_likes_created_at", table_name="article_likes", postgresql_include=[])
    op.drop_table("article_likes")
    op.drop_index(
        "uq_article_image_active",
        table_name="article_image_jobs",
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_article_image_jobs_article_id", table_name="article_image_jobs", postgresql_include=[]
    )
    op.drop_index(
        "ix_article_image_dispatch",
        table_name="article_image_jobs",
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.drop_table("article_image_jobs")
    op.drop_index(
        "uq_article_enrichment_active",
        table_name="article_enrichment_jobs",
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_article_enrichment_jobs_article_id",
        table_name="article_enrichment_jobs",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_article_enrichment_dispatch",
        table_name="article_enrichment_jobs",
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.drop_table("article_enrichment_jobs")
    op.drop_table("article_engagement")
    op.drop_table("article_contents")
    op.drop_index(
        "uq_article_analysis_active",
        table_name="article_analysis_jobs",
        postgresql_where="((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]))",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_article_analysis_jobs_article_id",
        table_name="article_analysis_jobs",
        postgresql_include=[],
    )
    op.drop_index(
        "ix_article_analysis_dispatch",
        table_name="article_analysis_jobs",
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.drop_table("article_analysis_jobs")
    op.drop_index("ix_user_accounts_created_at", table_name="user_accounts", postgresql_include=[])
    op.drop_table("user_accounts")
    op.drop_index(
        "ix_topics_identity_keys",
        table_name="topics",
        postgresql_using="gin",
        postgresql_include=[],
    )
    op.drop_table("topics")
    op.drop_table("taxonomy_migration_archive")
    op.drop_index(
        "ix_sources_due",
        table_name="sources",
        postgresql_where="((enabled IS TRUE) AND ((approval_status)::text = 'approved'::text))",
        postgresql_include=[],
    )
    op.drop_index("ix_sources_approval_status", table_name="sources", postgresql_include=[])
    op.drop_table("sources")
    op.drop_table("recommendation_topic_events")
    op.drop_table("recommendation_source_events")
    op.drop_table("overview_daily")
    op.drop_index(
        "ix_notification_delivery_dispatch",
        table_name="notification_deliveries",
        postgresql_where="((status)::text = 'queued'::text)",
        postgresql_include=[],
    )
    op.drop_table("notification_deliveries")
    op.drop_index(
        "ix_articles_search", table_name="articles", postgresql_using="gin", postgresql_include=[]
    )
    op.drop_index("ix_articles_published_to_feed_at", table_name="articles", postgresql_include=[])
    op.drop_index("ix_articles_feed", table_name="articles", postgresql_include=[])
    op.drop_index("ix_articles_discovered_at", table_name="articles", postgresql_include=[])
    op.drop_index(
        "ix_articles_automation_due",
        table_name="articles",
        postgresql_where="(((review_status)::text = 'pending'::text) AND ((publication_status)::text = 'unpublished'::text))",
        postgresql_include=[],
    )
    op.drop_table("articles")
    op.drop_table("admin_preferences")

    op.execute("DROP SEQUENCE article_slug_number")
    op.execute("DROP SEQUENCE topic_relationship_generation")
    op.execute("DROP SEQUENCE tag_topic_catalog_revision")
