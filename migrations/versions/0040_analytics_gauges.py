"""Publish compact current analytics gauges without historical PostgreSQL scans.

Revision ID: 0040
Revises: 0039
"""

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

JOBS = (
    "ingestion_jobs",
    "article_enrichment_jobs",
    "article_image_jobs",
    "source_enrichment_jobs",
    "article_analysis_jobs",
    "topic_analysis_jobs",
    "research_verification_jobs",
    "notification_deliveries",
)


def upgrade():
    op.execute("CREATE SEQUENCE analytics_gauge_revision AS bigint")
    op.execute("""CREATE TABLE analytics_gauge_refreshes (
        domain text NOT NULL CHECK(domain IN ('queues','feed','sources','topics')),
        shard smallint NOT NULL CHECK(shard BETWEEN 0 AND 255),
        revision bigint NOT NULL DEFAULT nextval('analytics_gauge_revision'),
        available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        bootstrap_required boolean NOT NULL DEFAULT false,
        bootstrap_generation bigint NOT NULL DEFAULT 0,
        PRIMARY KEY(domain,shard))""")
    op.execute("""CREATE TABLE analytics_gauge_bootstrap (
        singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
        started boolean NOT NULL DEFAULT false)""")
    op.execute("INSERT INTO analytics_gauge_bootstrap(singleton) VALUES(true)")
    op.execute(
        "CREATE INDEX ix_analytics_gauge_due "
        "ON analytics_gauge_refreshes(available_at,domain,shard)"
    )
    op.execute("""INSERT INTO analytics_gauge_refreshes(domain,shard)
        VALUES('queues',0),('feed',0),('sources',0),('topics',0)""")
    # Only live work enters these tiny covering indexes; completed history does
    # not increase the recurring queue-gauge scan. Build outside write locks.
    with op.get_context().autocommit_block():
        op.execute("SET max_parallel_maintenance_workers=0")
        for table in JOBS:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_{table}_analytics_active "
                f"ON {table}(status,created_at) WHERE status IN ('queued','running')"
            )
        op.execute("RESET max_parallel_maintenance_workers")


def downgrade():
    raise RuntimeError(
        "Native analytics requires a coordinated backup restore or forward repair; "
        "schema downgrade is not supported"
    )
