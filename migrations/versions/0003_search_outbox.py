"""Durable search-index changes for ORM, bulk SQL and cascading deletes."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLES = {
    "articles": (
        "title,slug,summary,ai_summary,ai_description,author,"
        "publication_status,review_status,feed_at"
    ),
    "topics": "name,slug,description,ai_description,aliases,keywords,status",
    "sources": "name,description,website_url,feed_url,approval_status,enabled",
    "tags": "name,slug,aliases",
}
LINKS = {
    "article_origins": ("sources", "source_id"),
    "article_topics": ("topics", "topic_id"),
    "article_tags": ("tags", "tag_id"),
}


def upgrade():
    op.create_table(
        "search_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("fanout", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("after_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind IN ('articles','topics','sources','tags')", name="ck_search_event_kind"
        ),
    )
    op.execute("""
    CREATE FUNCTION devfeed_search_entity_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE identifier uuid; field text; changed boolean := false;
    BEGIN
      IF TG_OP = 'UPDATE' THEN
        FOREACH field IN ARRAY string_to_array(TG_ARGV[1], ',') LOOP
          IF to_jsonb(OLD)->field IS DISTINCT FROM to_jsonb(NEW)->field THEN
            changed := true; EXIT;
          END IF;
        END LOOP;
        IF NOT changed THEN RETURN NEW; END IF;
      END IF;
      identifier := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
      INSERT INTO search_events(kind, entity_id, fanout)
        VALUES (TG_ARGV[0], identifier, TG_ARGV[0] <> 'articles');
      IF TG_ARGV[0] = 'articles' THEN
        INSERT INTO search_events(kind, entity_id)
          SELECT 'sources', source_id FROM article_origins WHERE article_id = identifier
          UNION SELECT 'topics', topic_id FROM article_topics WHERE article_id = identifier
          UNION SELECT 'tags', tag_id FROM article_tags WHERE article_id = identifier;
      END IF;
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$
    """)
    op.execute("""
    CREATE FUNCTION devfeed_search_link_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP <> 'INSERT' THEN
        INSERT INTO search_events(kind, entity_id) VALUES
          ('articles', OLD.article_id), (TG_ARGV[0], (to_jsonb(OLD)->>TG_ARGV[1])::uuid);
      END IF;
      IF TG_OP <> 'DELETE' THEN
        INSERT INTO search_events(kind, entity_id) VALUES
          ('articles', NEW.article_id), (TG_ARGV[0], (to_jsonb(NEW)->>TG_ARGV[1])::uuid);
      END IF;
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$
    """)
    for table, fields in TABLES.items():
        op.execute(f"""CREATE TRIGGER search_entity_changed
          AFTER INSERT OR UPDATE OF {fields} OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION devfeed_search_entity_changed('{table}', '{fields}')""")
    for table, (kind, field) in LINKS.items():
        op.execute(f"""CREATE TRIGGER search_link_changed
          AFTER INSERT OR UPDATE OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION devfeed_search_link_changed('{kind}', '{field}')""")
    # Offline initial backfill; normal search requests never enumerate the catalogue.
    for table in TABLES:
        op.execute(f"INSERT INTO search_events(kind, entity_id) SELECT '{table}', id FROM {table}")


def downgrade():
    for table in LINKS:
        op.execute(f"DROP TRIGGER search_link_changed ON {table}")
    for table in TABLES:
        op.execute(f"DROP TRIGGER search_entity_changed ON {table}")
    op.execute("DROP FUNCTION devfeed_search_link_changed()")
    op.execute("DROP FUNCTION devfeed_search_entity_changed()")
    op.drop_table("search_events")
