"""Checkpoint retention sweeps and index retained-success successor probes."""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

SUBJECTS = (
    ("article_analysis_jobs", "article_id"),
    ("topic_analysis_jobs", "proposal_id"),
    ("topic_analysis_jobs", "topic_id"),
)


def upgrade():
    # Concurrent index builds commit preceding DDL. Both table creation and the
    # initial rows must therefore tolerate retry after a failed/interrupted build.
    op.create_table(
        "job_payload_retention",
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cutoff", sa.DateTime(timezone=True)),
        sa.Column("cursor_finished_at", sa.DateTime(timezone=True)),
        sa.Column("cursor_id", sa.Uuid()),
        sa.CheckConstraint("kind IN ('article','topic')", name="ck_job_payload_retention_kind"),
        sa.CheckConstraint(
            "(cursor_finished_at IS NULL) = (cursor_id IS NULL)",
            name="ck_job_payload_retention_cursor",
        ),
        if_not_exists=True,
    )
    op.execute(
        "INSERT INTO job_payload_retention (kind, next_run_at) "
        "VALUES ('article', now()), ('topic', now()) ON CONFLICT (kind) DO NOTHING"
    )
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        try:
            for table, subject in SUBJECTS:
                name = f"ix_{table}_{subject}_retained_success"
                invalid = op.get_bind().scalar(
                    sa.text(
                        "SELECT NOT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)"
                    ),
                    {"name": name},
                )
                if invalid:
                    op.execute(f"DROP INDEX CONCURRENTLY {name}")
                op.execute(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                    f"ON {table} ({subject}, created_at, id) "
                    "WHERE status = 'succeeded' AND inputs_pruned_at IS NULL "
                    f"AND {subject} IS NOT NULL"
                )
        finally:
            op.execute("RESET lock_timeout")


def downgrade():
    for table, subject in reversed(SUBJECTS):
        op.drop_index(f"ix_{table}_{subject}_retained_success", table_name=table)
    op.drop_table("job_payload_retention")
