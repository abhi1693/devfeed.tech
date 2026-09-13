"""Record each inference invocation without storing its prompt or response."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inference_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("operation", sa.String(50), nullable=False),
        sa.Column("job_id", sa.Uuid()),
        sa.Column("attempt", sa.Integer()),
        sa.Column("reason", sa.String(100)),
        sa.Column("model", sa.String(100)),
        sa.Column("reasoning_effort", sa.String(30)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("tokens", postgresql.JSONB(), nullable=False),
        sa.Column("web_searches", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
    )
    op.create_index("ix_inference_calls_started_at", "inference_calls", ["started_at"])
    op.create_index("ix_inference_calls_job_id", "inference_calls", ["job_id"])


def downgrade():
    op.drop_table("inference_calls")
