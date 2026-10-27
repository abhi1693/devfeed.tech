"""Publish compact current analytics gauges without historical PostgreSQL scans.

Revision ID: 0040
Revises: 0039
"""

from alembic import op
from sqlalchemy import text

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


def active_index_state(connection, table):
    """Reject name collisions; only an identical interrupted build is replaceable."""
    name = f"ix_{table}_analytics_active"
    state = (
        connection.execute(
            text("""SELECT c.relkind, i.indisvalid, i.indisready, i.indislive,
            i.indisunique, i.indisexclusion, i.indnatts, i.indnkeyatts,
            i.indrelid = to_regclass(:table) AS correct_table, am.amname,
            pg_get_indexdef(i.indexrelid,1,false) AS first_key,
            pg_get_indexdef(i.indexrelid,2,false) AS second_key,
            pg_get_expr(i.indpred,i.indrelid) AS predicate
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_index i ON i.indexrelid=c.oid
            LEFT JOIN pg_am am ON am.oid=c.relam
            WHERE n.nspname=current_schema() AND c.relname=:name"""),
            {"table": table, "name": name},
        )
        .mappings()
        .one_or_none()
    )
    if state is None:
        return None
    expected_predicate = (
        "((status)::text = ANY ((ARRAY['queued'::character varying, "
        "'running'::character varying])::text[]))"
    )
    if not (
        state["relkind"] == "i"
        and state["correct_table"]
        and state["amname"] == "btree"
        and not state["indisunique"]
        and not state["indisexclusion"]
        and state["indnatts"] == state["indnkeyatts"] == 2
        and state["first_key"] == "status"
        and state["second_key"] == "created_at"
        and state["predicate"] == expected_predicate
    ):
        raise RuntimeError(f"Existing index {name} does not match migration0040; repair explicitly")
    return bool(state["indisvalid"] and state["indisready"] and state["indislive"])


def create_active_index(connection, table, *, concurrently):
    keyword = "CONCURRENTLY " if concurrently else ""
    connection.execute(
        text(
            f"CREATE INDEX {keyword}ix_{table}_analytics_active "
            f"ON {table}(status,created_at) WHERE status IN ('queued','running')"
        )
    )
    if active_index_state(connection, table) is not True:
        raise RuntimeError(f"Index preparation did not finish for {table}")


def prepare_active_indexes(connection):
    """Run before the upgrade, retaining the caller's timeouts and worker budget."""
    previous = connection.scalar(text("SHOW max_parallel_maintenance_workers"))
    try:
        connection.execute(text("SET max_parallel_maintenance_workers=0"))
        for table in JOBS:
            state = active_index_state(connection, table)
            if state is True:
                continue
            if state is False:
                connection.execute(text(f"DROP INDEX CONCURRENTLY ix_{table}_analytics_active"))
            create_active_index(connection, table, concurrently=True)
    finally:
        connection.execute(
            text("SELECT set_config('max_parallel_maintenance_workers',:previous,false)"),
            {"previous": previous},
        )


def upgrade():
    if op.get_context().as_sql:
        raise RuntimeError(
            "Migration0040 requires an online upgrade to prepare indexes before the cutover"
        )
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
    # Existing installations prepared these concurrently before any upgrade
    # step. Keep the cutover atomic: an autocommit block here would expose0039
    # and make the previous application unready while preparation was running.
    # Fresh installations have only empty tables and need no concurrent build.
    for table in JOBS:
        state = active_index_state(op.get_bind(), table)
        if state is None:
            create_active_index(op.get_bind(), table, concurrently=False)
        elif state is False:
            raise RuntimeError(f"Prepare invalid analytics index for {table} before upgrading")


def downgrade():
    raise RuntimeError(
        "Native analytics requires a coordinated backup restore or forward repair; "
        "schema downgrade is not supported"
    )
