"""Persist daily personalized Must Reads."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_must_reads",
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("selection_date", sa.Date(), primary_key=True),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("picks", postgresql.JSONB(), nullable=False),
        sa.Column("presented_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("user_must_reads")
