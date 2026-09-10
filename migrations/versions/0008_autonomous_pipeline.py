"""DevFeed 0.0.1: evidence-aware automation, resumable research and usage history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

app_version = "0.0.1"
revision = "0008_autonomous_pipeline"
down_revision = "0007_admin_preferences"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sources",
        sa.Column("publication_policy", sa.String(20), nullable=False, server_default="manual"),
    )
    op.add_column(
        "sources",
        sa.Column("publication_policy_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_source_publication_policy",
        "sources",
        "publication_policy IN ('manual','preview','auto')",
    )
    op.add_column(
        "topic_proposals",
        sa.Column("research_requested", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("article_analysis_jobs", sa.Column("catalog_hash", sa.String(64)))
    for table in ("article_analysis_jobs", "topic_analysis_jobs"):
        op.add_column(
            table, sa.Column("usage", postgresql.JSONB(), nullable=False, server_default="{}")
        )
        op.add_column(
            table, sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0")
        )
    op.add_column(
        "article_reviews",
        sa.Column("automation", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "topic_reanalysis",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "topic_id", sa.Uuid(), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("topic_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("after_article_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_topic_reanalysis_pending",
        "topic_reanalysis",
        ["created_at"],
        postgresql_where=sa.text("finished_at IS NULL"),
    )
    op.create_table(
        "source_publication_policy_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_id", sa.Uuid(), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_source_publication_policy_reviews_source_id",
        "source_publication_policy_reviews",
        ["source_id"],
    )
    op.create_table(
        "article_publication_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("decision", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_article_publication_decisions_article_id",
        "article_publication_decisions",
        ["article_id"],
    )


def downgrade():
    op.drop_table("article_publication_decisions")
    op.drop_table("source_publication_policy_reviews")
    op.drop_table("topic_reanalysis")
    op.drop_column("article_reviews", "automation")
    for table in ("article_analysis_jobs", "topic_analysis_jobs"):
        op.drop_column(table, "duration_ms")
        op.drop_column(table, "usage")
    op.drop_column("article_analysis_jobs", "catalog_hash")
    op.drop_column("topic_proposals", "research_requested")
    op.drop_constraint("ck_source_publication_policy", "sources", type_="check")
    op.drop_column("sources", "publication_policy_revision")
    op.drop_column("sources", "publication_policy")
