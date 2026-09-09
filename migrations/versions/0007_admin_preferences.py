"""DevFeed 0.0.1: account preferences independent of login sessions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

app_version = "0.0.1"
revision = "0007_admin_preferences"
down_revision = "0006_relationship_research"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admin_preferences",
        sa.Column("owner_key", sa.String(64), primary_key=True),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("admin_preferences")
