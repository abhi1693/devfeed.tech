"""Optional reader accounts and followed topics.

Revision ID: 0014_reader_accounts
Revises: 0013_article_automation
"""

import sqlalchemy as sa
from alembic import op

app_version = "0.0.1"
revision = "0014_reader_accounts"
down_revision = "0013_article_automation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reader_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("issuer", "subject", name="uq_reader_identity"),
    )
    op.create_table(
        "reader_topics",
        sa.Column(
            "reader_id",
            sa.Uuid(),
            sa.ForeignKey("reader_accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "topic_id", sa.Uuid(), sa.ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reader_topics_topic_id", "reader_topics", ["topic_id"])


def downgrade():
    op.drop_table("reader_topics")
    op.drop_table("reader_accounts")
