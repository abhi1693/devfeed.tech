"""Store processed topic logos through the durable Images queue."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "topics", sa.Column("managed_logo", postgresql.JSONB(), nullable=False, server_default="{}")
    )
    op.alter_column("article_image_jobs", "article_id", nullable=True)
    op.add_column(
        "article_image_jobs",
        sa.Column("topic_id", sa.UUID(), sa.ForeignKey("topics.id", ondelete="CASCADE")),
    )
    op.add_column("article_image_jobs", sa.Column("storage_version", sa.String(20)))
    op.create_index("ix_article_image_jobs_topic_id", "article_image_jobs", ["topic_id"])
    op.create_index(
        "uq_topic_image_active",
        "article_image_jobs",
        ["topic_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_check_constraint(
        "ck_image_job_subject",
        "article_image_jobs",
        "(article_id IS NOT NULL AND topic_id IS NULL AND operation <> 'topic-logo') OR "
        "(article_id IS NULL AND topic_id IS NOT NULL AND operation = 'topic-logo')",
    )


def downgrade():
    op.execute("DELETE FROM article_image_jobs WHERE topic_id IS NOT NULL")
    op.drop_constraint("ck_image_job_subject", "article_image_jobs", type_="check")
    op.drop_index("uq_topic_image_active", "article_image_jobs")
    op.drop_index("ix_article_image_jobs_topic_id", "article_image_jobs")
    op.drop_column("article_image_jobs", "storage_version")
    op.drop_column("article_image_jobs", "topic_id")
    op.alter_column("article_image_jobs", "article_id", nullable=False)
    op.drop_column("topics", "managed_logo")
