"""Record evidence for source suggestion relevance decisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

app_version = "0.0.1"
revision = "0029_source_relevance"
down_revision = "0028_overview_daily"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sources",
        sa.Column(
            "relevance_assessment",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade():
    op.drop_column("sources", "relevance_assessment")
