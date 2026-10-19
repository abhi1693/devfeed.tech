"""Separate source profile fetching from independently retryable relevance work.

Legacy rows retain their old operation and evidence. Drain coupled source workers
before new workers can dispatch split operations. No source decision is changed.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sources",
        sa.Column("processing_request", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "source_enrichment_jobs",
        sa.Column("operation", sa.String(20), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "source_enrichment_jobs",
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_check_constraint(
        "ck_source_enrichment_operation",
        "source_enrichment_jobs",
        "operation IN ('legacy','profile','relevance')",
    )
    op.create_index(
        "ix_source_enrichment_current",
        "source_enrichment_jobs",
        ["source_id", "operation", sa.text("created_at DESC"), sa.text("id DESC")],
    )


def downgrade():
    # Operators must drain split-operation deliveries before restoring old workers.
    connection = op.get_bind()
    active = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM source_enrichment_jobs "
            "WHERE operation <> 'legacy' AND status IN ('queued','running'))"
        )
    )
    if active:
        raise RuntimeError("Drain source profile/relevance work before downgrading")
    if connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM sources WHERE processing_request <> '{}'::jsonb)")
    ):
        raise RuntimeError("Resolve explicit source processing requests before downgrading")
    op.drop_index("ix_source_enrichment_current", table_name="source_enrichment_jobs")
    op.drop_constraint("ck_source_enrichment_operation", "source_enrichment_jobs", type_="check")
    op.drop_column("source_enrichment_jobs", "result")
    op.drop_column("source_enrichment_jobs", "operation")
    op.drop_column("sources", "processing_request")
