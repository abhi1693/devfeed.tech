"""Validate maintenance ownership and build sparse links without blocking job writes.

Revision ID: 0041
Revises: 0040
"""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

JOB_TABLES = ("article_enrichment_jobs", "article_analysis_jobs", "article_image_jobs")
PREDICATE = "refresh_id IS NOT NULL"


def _repair_interrupted_index(name, table):
    if op.get_context().as_sql:
        return
    current = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT parent.relname AS table_name,idx.relkind AS relation_kind,"
                "i.indisvalid,i.indisunique,method.amname AS method,i.indnatts AS attributes,"
                "i.indexprs IS NOT NULL AS expressions,"
                "pg_get_expr(i.indpred,i.indrelid) AS predicate,"
                "ARRAY(SELECT a.attname::text FROM unnest(i.indkey) "
                "WITH ORDINALITY AS key(attnum,position) JOIN pg_attribute a "
                "ON a.attrelid=parent.oid AND a.attnum=key.attnum ORDER BY key.position) "
                "AS columns FROM pg_class idx JOIN pg_namespace ns ON ns.oid=idx.relnamespace "
                "LEFT JOIN pg_am method ON method.oid=idx.relam "
                "LEFT JOIN pg_index i ON i.indexrelid=idx.oid "
                "LEFT JOIN pg_class parent ON parent.oid=i.indrelid "
                "WHERE ns.nspname=current_schema() AND idx.relname=:name"
            ),
            {"name": name},
        )
        .mappings()
        .one_or_none()
    )
    if current is None:
        return
    if (
        current["relation_kind"] != "i"
        or current["table_name"] != table
        or current["columns"] != ["refresh_id"]
        or current["indisunique"]
        or current["predicate"] != "(refresh_id IS NOT NULL)"
        or current["method"] != "btree"
        or current["attributes"] != 1
        or current["expressions"]
    ):
        raise RuntimeError(f"Unexpected definition for maintenance index {name}")
    if not current["indisvalid"]:
        op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)


def upgrade():
    # Schema DDL belongs to the committed 0040 transaction. A failed concurrent
    # index build leaves that revision intact, so the repair can safely resume.
    with op.get_context().autocommit_block():
        for table in JOB_TABLES:
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT fk_{table}_refresh_id")
            name = f"ix_{table}_refresh_id"
            _repair_interrupted_index(name, table)
            op.create_index(
                name,
                table,
                ["refresh_id"],
                postgresql_where=sa.text(PREDICATE),
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade():
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM article_refreshes) THEN
            RAISE EXCEPTION 'Cannot discard article maintenance history; roll forward';
          END IF;
        END $$
    """)
    with op.get_context().autocommit_block():
        for table in reversed(JOB_TABLES):
            op.drop_index(
                f"ix_{table}_refresh_id",
                table_name=table,
                postgresql_concurrently=True,
                if_exists=True,
            )
