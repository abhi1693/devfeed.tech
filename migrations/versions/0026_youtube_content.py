"""Add explicit YouTube resources, video identity and retained collection membership."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

SOURCE_RESOURCE = (
    "(provider = 'rss' AND resource_kind = 'feed' AND provider_resource_id IS NULL "
    "AND provider_collection_id IS NULL AND recurring) OR "
    "(provider = 'youtube' AND feed_url IS NULL AND provider_resource_id IS NOT NULL "
    "AND ((resource_kind IN ('channel','playlist') AND recurring "
    "AND provider_collection_id IS NOT NULL) OR "
    "(resource_kind = 'video' AND NOT recurring AND provider_collection_id IS NULL)))"
)
ARTICLE_RESOURCE = (
    "(provider IS NULL AND provider_resource_id IS NULL) OR "
    "(provider IS NOT NULL AND provider = 'youtube' AND provider_resource_id IS NOT NULL "
    "AND provider_resource_id ~ '^[A-Za-z0-9_-]{11}$' AND content_format = 'video')"
)
OLD_SEARCH_FIELDS = (
    "title,slug,summary,ai_summary,ai_description,author,publication_status,review_status,feed_at"
)


def search_trigger(fields):
    op.execute("DROP TRIGGER search_entity_changed ON articles")
    op.execute(f"""CREATE TRIGGER search_entity_changed
        AFTER INSERT OR UPDATE OF {fields} OR DELETE ON articles
        FOR EACH ROW EXECUTE FUNCTION devfeed_search_entity_changed('articles', '{fields}')""")


def upgrade():
    for column in (
        sa.Column("provider", sa.String(20), nullable=False, server_default="rss"),
        sa.Column("resource_kind", sa.String(20), nullable=False, server_default="feed"),
        sa.Column("provider_resource_id", sa.String(128)),
        sa.Column("provider_collection_id", sa.String(128)),
        sa.Column("provider_state", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("recurring", sa.Boolean(), nullable=False, server_default=sa.true()),
    ):
        op.add_column("sources", column)
    op.drop_constraint("ck_sources_approved_feed", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_approved_feed",
        "sources",
        "approval_status <> 'approved' OR feed_url IS NOT NULL OR provider = 'youtube'",
    )
    op.create_check_constraint("ck_sources_provider_resource", "sources", SOURCE_RESOURCE)
    op.create_unique_constraint(
        "uq_sources_provider_resource",
        "sources",
        ["provider", "resource_kind", "provider_resource_id"],
    )
    op.add_column("articles", sa.Column("provider", sa.String(20)))
    op.add_column("articles", sa.Column("provider_resource_id", sa.String(128)))
    op.add_column(
        "articles",
        sa.Column("video_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_unique_constraint(
        "uq_articles_provider_resource", "articles", ["provider", "provider_resource_id"]
    )
    op.create_check_constraint("ck_articles_provider_resource", "articles", ARTICLE_RESOURCE)
    op.add_column(
        "article_origins",
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "youtube_memberships",
        sa.Column(
            "source_id",
            sa.UUID(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "article_id",
            sa.UUID(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("playlist_item_id", sa.String(128)),
        sa.Column("added_at", sa.DateTime(timezone=True)),
        sa.Column("seen_generation", sa.UUID()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    search_trigger(
        OLD_SEARCH_FIELDS + ",provider,provider_resource_id,video_metadata,content_format"
    )


def downgrade():
    # Restore the old invariant without publishing orphaned video content.
    op.execute("UPDATE articles SET publication_status='unpublished' WHERE provider='youtube'")
    op.execute(
        "UPDATE sources SET approval_status='pending', enabled=false WHERE provider='youtube'"
    )
    search_trigger(OLD_SEARCH_FIELDS)
    op.drop_table("youtube_memberships")
    op.drop_column("article_origins", "active")
    op.drop_constraint("ck_articles_provider_resource", "articles", type_="check")
    op.drop_constraint("uq_articles_provider_resource", "articles", type_="unique")
    for name in ("video_metadata", "provider_resource_id", "provider"):
        op.drop_column("articles", name)
    op.drop_constraint("uq_sources_provider_resource", "sources", type_="unique")
    op.drop_constraint("ck_sources_provider_resource", "sources", type_="check")
    op.drop_constraint("ck_sources_approved_feed", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_approved_feed",
        "sources",
        "approval_status <> 'approved' OR feed_url IS NOT NULL",
    )
    for name in (
        "recurring",
        "provider_state",
        "provider_collection_id",
        "provider_resource_id",
        "resource_kind",
        "provider",
    ):
        op.drop_column("sources", name)
