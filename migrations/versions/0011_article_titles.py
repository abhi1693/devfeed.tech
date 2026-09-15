"""Preserve source titles alongside evidence-based editorial titles."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("articles", sa.Column("ai_title", sa.String(200), nullable=True))


def downgrade():
    op.drop_column("articles", "ai_title")
