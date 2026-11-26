"""Track AI usage and serialize rolling-window admissions in PostgreSQL."""

from alembic import context, op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE inference_usage_ledger (
        started_at timestamptz NOT NULL,
        id uuid NOT NULL,
        task text NOT NULL,
        revision smallint NOT NULL CHECK (revision IN (0, 1)),
        usage jsonb NOT NULL CHECK (jsonb_typeof(usage) = 'object'),
        PRIMARY KEY (started_at, id)
    )""")
    op.execute("""CREATE INDEX ix_inference_relationship_window
        ON inference_usage_ledger (started_at)
        WHERE task IN ('relationship_research','relationship_verification')""")
    op.execute("""CREATE TABLE inference_quota_cutover (
        singleton boolean PRIMARY KEY CHECK (singleton),
        verified_at timestamptz NOT NULL
    )""")

    if context.config.attributes.get("fresh_database", False):
        op.execute("INSERT INTO inference_quota_cutover VALUES (true,statement_timestamp())")


def downgrade():
    op.drop_table("inference_quota_cutover")
    op.drop_table("inference_usage_ledger")
