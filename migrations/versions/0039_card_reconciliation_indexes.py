"""Bound card dependency lookups while source and worker writes continue.

Revision ID: 0039
Revises: 0038
"""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_source_candidates_source_lookup", "source_candidates", ["source_id", "id"]),
    ("ix_source_discovery_candidate_lookup", "source_discovery_jobs", ["candidate_id", "id"]),
)


def _repair_interrupted_index(name, table, columns):
    if op.get_context().as_sql:
        return
    current = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT parent.relname AS table_name,i.indisvalid,i.indisunique,"
                "method.amname AS method,i.indnatts AS attributes,"
                "i.indexprs IS NOT NULL AS expressions,"
                "i.indpred IS NOT NULL AS partial,ARRAY(SELECT a.attname::text "
                "FROM unnest(i.indkey) WITH ORDINALITY AS key(attnum,position) "
                "JOIN pg_attribute a ON a.attrelid=parent.oid AND a.attnum=key.attnum "
                "ORDER BY key.position) AS columns FROM pg_class idx "
                "JOIN pg_namespace ns ON ns.oid=idx.relnamespace "
                "JOIN pg_am method ON method.oid=idx.relam "
                "JOIN pg_index i ON i.indexrelid=idx.oid "
                "JOIN pg_class parent ON parent.oid=i.indrelid "
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
        current["table_name"] != table
        or current["columns"] != columns
        or current["indisunique"]
        or current["partial"]
        or current["method"] != "btree"
        or current["attributes"] != len(columns)
        or current["expressions"]
    ):
        raise RuntimeError(f"Unexpected definition for reconciliation index {name}")
    if not current["indisvalid"]:
        op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)


def upgrade():
    with op.get_context().autocommit_block():
        for name, table, columns in INDEXES:
            _repair_interrupted_index(name, table, columns)
            op.create_index(name, table, columns, postgresql_concurrently=True, if_not_exists=True)


def downgrade():
    with op.get_context().autocommit_block():
        for name, table, _columns in reversed(INDEXES):
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
