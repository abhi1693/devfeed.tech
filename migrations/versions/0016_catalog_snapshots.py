"""Version classification snapshots transactionally and cover public facet reads."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

TABLES = {
    "topics": "id, name, slug, aliases, kind, keywords, status",
    "tags": "id, name, slug, aliases",
    "topic_proposals": "id, proposed, status",
}


def upgrade():
    op.create_table(
        "catalog_revisions",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column(
            "revision", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
    )
    for table, columns in TABLES.items():
        op.execute(f"INSERT INTO catalog_revisions(name) VALUES ('{table}')")
        op.execute(f"""
            CREATE FUNCTION refresh_{table}_revision() RETURNS trigger
            LANGUAGE plpgsql AS $fn$
            BEGIN
              IF TG_OP = 'INSERT' THEN
                IF NOT EXISTS (SELECT 1 FROM new_rows) THEN RETURN NULL; END IF;
              ELSIF TG_OP = 'DELETE' THEN
                IF NOT EXISTS (SELECT 1 FROM old_rows) THEN RETURN NULL; END IF;
              ELSIF TG_OP = 'UPDATE' THEN
                IF NOT EXISTS (
                  SELECT {columns} FROM new_rows EXCEPT SELECT {columns} FROM old_rows
                ) THEN RETURN NULL; END IF;
              END IF;
              INSERT INTO catalog_revisions(name) VALUES (TG_TABLE_NAME)
              ON CONFLICT (name) DO UPDATE SET revision = gen_random_uuid();
              RETURN NULL;
            END $fn$
        """)
        for operation, referencing in (
            ("INSERT", "REFERENCING NEW TABLE AS new_rows"),
            ("DELETE", "REFERENCING OLD TABLE AS old_rows"),
            ("UPDATE", "REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows"),
            ("TRUNCATE", ""),
        ):
            op.execute(f"""
                CREATE TRIGGER catalog_revision_{operation.lower()} AFTER {operation} ON {table}
                {referencing} FOR EACH STATEMENT EXECUTE FUNCTION refresh_{table}_revision()
            """)
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            op.execute("""
                CREATE INDEX CONCURRENTLY ix_articles_public_facets
                ON articles (content_type, language, id)
                WHERE publication_status = 'published' AND review_status = 'approved'
            """)
            op.execute("""
                CREATE INDEX CONCURRENTLY ix_article_origins_article_source
                ON article_origins (article_id, source_id)
            """)
        finally:
            op.execute("RESET lock_timeout")


def downgrade():
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_article_origins_article_source")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_articles_public_facets")
    for table in TABLES:
        for operation in ("insert", "delete", "update", "truncate"):
            op.execute(f"DROP TRIGGER catalog_revision_{operation} ON {table}")
        op.execute(f"DROP FUNCTION refresh_{table}_revision()")
    op.drop_table("catalog_revisions")
