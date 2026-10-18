"""Invalidate visible-tag counts transactionally, including raw SQL and revocations."""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

TABLES = {
    "articles": "id, publication_status, review_status",
    "sources": "id, approval_status",
    "article_origins": "id, article_id, source_id",
    "article_tags": "article_id, tag_id",
}


def upgrade():
    op.execute("INSERT INTO catalog_revisions(name) VALUES ('search_tag_visibility')")
    for table, columns in TABLES.items():
        op.execute(f"""
            CREATE FUNCTION refresh_search_tags_{table}() RETURNS trigger
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
              INSERT INTO catalog_revisions(name) VALUES ('search_tag_visibility')
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
                CREATE TRIGGER search_tags_{table}_{operation.lower()} AFTER {operation} ON {table}
                {referencing} FOR EACH STATEMENT EXECUTE FUNCTION refresh_search_tags_{table}()
            """)


def downgrade():
    for table in reversed(TABLES):
        for operation in ("insert", "delete", "update", "truncate"):
            op.execute(f"DROP TRIGGER search_tags_{table}_{operation} ON {table}")
        op.execute(f"DROP FUNCTION refresh_search_tags_{table}()")
    op.execute("DELETE FROM catalog_revisions WHERE name='search_tag_visibility'")
