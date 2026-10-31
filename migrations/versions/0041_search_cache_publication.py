"""Keep source-change invalidation distinct from index-only projection work.

Revision ID: 0041
Revises: 0040
"""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    # PostgreSQL's constant default preserves every retained/unknown event as a
    # conservative public-data invalidation without rewriting the outbox.
    op.add_column(
        "search_events",
        sa.Column("public_change", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_column("search_events", "public_change")
