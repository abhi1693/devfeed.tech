"""Manage official product logos through the durable Images queue."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

OLD_SUBJECT = (
    "(article_id IS NOT NULL AND topic_id IS NULL AND source_id IS NULL "
    "AND operation NOT IN ('topic-logo','source-logo','source-logo-refresh')) OR "
    "(article_id IS NULL AND topic_id IS NOT NULL AND source_id IS NULL "
    "AND operation = 'topic-logo') OR "
    "(article_id IS NULL AND topic_id IS NULL AND source_id IS NOT NULL "
    "AND operation IN ('source-logo','source-logo-refresh'))"
)
NEW_SUBJECT = (
    "(article_id IS NOT NULL AND topic_id IS NULL AND source_id IS NULL AND product_id IS NULL "
    "AND operation NOT IN ('topic-logo','source-logo','source-logo-refresh','product-logo')) OR "
    "(article_id IS NULL AND topic_id IS NOT NULL AND source_id IS NULL AND product_id IS NULL "
    "AND operation = 'topic-logo') OR "
    "(article_id IS NULL AND topic_id IS NULL AND source_id IS NOT NULL AND product_id IS NULL "
    "AND operation IN ('source-logo','source-logo-refresh')) OR "
    "(article_id IS NULL AND topic_id IS NULL AND source_id IS NULL AND product_id IS NOT NULL "
    "AND operation = 'product-logo')"
)


def upgrade():
    op.add_column(
        "partner_products",
        sa.Column("managed_logo", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "article_image_jobs",
        sa.Column(
            "product_id", sa.UUID(), sa.ForeignKey("partner_products.id", ondelete="CASCADE")
        ),
    )
    op.create_index("ix_article_image_jobs_product_id", "article_image_jobs", ["product_id"])
    op.create_index(
        "uq_product_image_active",
        "article_image_jobs",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.drop_constraint("ck_image_job_subject", "article_image_jobs", type_="check")
    op.create_check_constraint("ck_image_job_subject", "article_image_jobs", NEW_SUBJECT)


def downgrade():
    op.execute("DELETE FROM article_image_jobs WHERE product_id IS NOT NULL")
    op.drop_constraint("ck_image_job_subject", "article_image_jobs", type_="check")
    op.create_check_constraint("ck_image_job_subject", "article_image_jobs", OLD_SUBJECT)
    op.drop_index("uq_product_image_active", "article_image_jobs")
    op.drop_index("ix_article_image_jobs_product_id", "article_image_jobs")
    op.drop_column("article_image_jobs", "product_id")
    op.drop_column("partner_products", "managed_logo")
