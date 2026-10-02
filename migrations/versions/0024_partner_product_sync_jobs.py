"""Independently retryable product sync jobs and resumable discovery."""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "partner_pipeline_jobs",
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("partner_pipeline_jobs.id")),
    )
    op.add_column("partner_pipeline_jobs", sa.Column("external_id", sa.String(200)))
    op.create_index("ix_partner_pipeline_jobs_parent_id", "partner_pipeline_jobs", ["parent_id"])
    op.create_unique_constraint(
        "uq_partner_product_sync", "partner_pipeline_jobs", ["parent_id", "external_id"]
    )
    op.drop_constraint("ck_partner_pipeline_operation", "partner_pipeline_jobs", type_="check")
    op.create_check_constraint(
        "ck_partner_pipeline_operation",
        "partner_pipeline_jobs",
        "operation IN ('sync','sync_product','assess')",
    )
    # Fence old workers and retain page checkpoints from the former page-at-a-time pipeline.
    op.execute("""UPDATE partner_pipeline_jobs SET status = 'queued', attempts = 0,
        dispatched_at = NULL, lease_until = NULL, lease_token = NULL,
        available_at = now(), payload = payload || '{"pipeline_version": 2}'::jsonb
        WHERE operation = 'sync' AND status IN ('queued', 'running')""")


def downgrade():
    raise RuntimeError("Restore a database backup to revert durable product sync jobs")
