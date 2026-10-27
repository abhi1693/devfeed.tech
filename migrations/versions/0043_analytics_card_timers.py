"""Index current-card deadlines outside population-wide analytical scans.

Revision ID: 0043
Revises: 0042
"""

from alembic import op
from sqlalchemy import text

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def refresh_index_state(connection):
    """Accept only the exact index definition and replace interrupted builds."""
    state = (
        connection.execute(
            text("""SELECT c.relkind,i.indisvalid,i.indisready,i.indislive,
                i.indisunique,i.indisexclusion,i.indnatts,i.indnkeyatts,
                i.indrelid=to_regclass('analytics_card_refresh') AS correct_table,
                am.amname,pg_get_expr(i.indpred,i.indrelid) AS predicate,
                pg_get_indexdef(i.indexrelid,1,false) AS first_key,
                pg_get_indexdef(i.indexrelid,2,false) AS second_key,
                pg_get_indexdef(i.indexrelid,3,false) AS third_key,
                i.indoption[0] AS first_option,i.indoption[1] AS second_option,
                i.indoption[2] AS third_option
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                LEFT JOIN pg_index i ON i.indexrelid=c.oid
                LEFT JOIN pg_am am ON am.oid=c.relam
                WHERE n.nspname=current_schema() AND c.relname=:name"""),
            {"name": "ix_analytics_card_refresh_initial_due"},
        )
        .mappings()
        .one_or_none()
    )
    if state is None:
        return None
    if not (
        state["relkind"] == "i"
        and state["correct_table"]
        and state["amname"] == "btree"
        and not state["indisunique"]
        and not state["indisexclusion"]
        and state["indnatts"] == state["indnkeyatts"] == 3
        and (state["first_key"], state["second_key"], state["third_key"])
        == ("available_at", "kind", "entity_id")
        and (state["first_option"], state["second_option"], state["third_option"]) == (0, 0, 0)
        and state["predicate"] == "initial_pending"
    ):
        raise RuntimeError(
            "Existing index ix_analytics_card_refresh_initial_due does not match 0043"
        )
    return bool(state["indisvalid"] and state["indisready"] and state["indislive"])


def create_refresh_index(connection, *, concurrently):
    keyword = "CONCURRENTLY " if concurrently else ""
    connection.execute(
        text(
            "CREATE INDEX " + keyword + "ix_analytics_card_refresh_initial_due "
            "ON analytics_card_refresh(available_at,kind,entity_id) WHERE initial_pending"
        )
    )
    if refresh_index_state(connection) is not True:
        raise RuntimeError("Analytics card refresh index preparation did not finish")


def prepare_refresh_index(connection):
    """Build existing 0042 queue indexes before the transactional revision change."""
    if connection.scalar(text("SELECT to_regclass('analytics_card_refresh')")) is None:
        return
    previous = connection.scalar(text("SHOW max_parallel_maintenance_workers"))
    try:
        connection.execute(text("SET max_parallel_maintenance_workers=0"))
        state = refresh_index_state(connection)
        if state is True:
            return
        if state is False:
            connection.execute(
                text("DROP INDEX CONCURRENTLY ix_analytics_card_refresh_initial_due")
            )
        create_refresh_index(connection, concurrently=True)
    finally:
        connection.execute(
            text("SELECT set_config('max_parallel_maintenance_workers',:previous,false)"),
            {"previous": previous},
        )


def require_refresh_index(connection):
    state = refresh_index_state(connection)
    if state is True:
        return
    if state is False:
        raise RuntimeError("Prepare the invalid analytics card refresh index before upgrading")
    created_here = connection.scalar(
        text("""SELECT c.xmin::text::bigint = txid_current() % 4294967296
            FROM pg_class c WHERE c.oid=to_regclass('analytics_card_refresh')""")
    )
    if not created_here:
        raise RuntimeError("Prepare the analytics card refresh index before upgrading")
    create_refresh_index(connection, concurrently=False)


def upgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Migration0043 requires an online upgrade to prepare its index")
    require_refresh_index(op.get_bind())
    # Fence a pre-0043 exporter before it can publish cards without timers.
    # PostgreSQL invalidates cached plans that still refer to the old name.
    op.execute(
        "ALTER TABLE analytics_pipeline_state RENAME COLUMN lease_token TO native_lease_token"
    )
    op.execute("UPDATE analytics_pipeline_state SET native_lease_token=NULL,lease_until=NULL")
    op.execute("ALTER TABLE analytics_rollup_compaction ADD COLUMN last_prune_token text")
    kinds = "'article','source','candidate','topic-proposal','relationship-proposal'"
    op.execute(f"""CREATE TABLE analytics_card_timers (
        policy text NOT NULL, kind text NOT NULL, entity_id uuid NOT NULL,
        epoch bigint NOT NULL, generation bigint NOT NULL, available_at timestamptz,
        PRIMARY KEY(policy,kind,entity_id),
        CONSTRAINT ck_analytics_card_timers_kind CHECK (kind IN ({kinds})))""")
    op.execute("""CREATE INDEX ix_analytics_card_timers_due
        ON analytics_card_timers(policy,epoch,available_at,kind,entity_id)
        WHERE available_at IS NOT NULL""")
    op.execute("""CREATE INDEX ix_analytics_card_timers_epoch
        ON analytics_card_timers(policy,epoch,kind,entity_id)""")
    op.execute("""CREATE INDEX ix_analytics_card_timers_tombstone
        ON analytics_card_timers(policy,epoch,kind,entity_id)
        WHERE available_at IS NULL""")
    op.execute("""CREATE TABLE analytics_card_timer_bootstrap (
        singleton boolean PRIMARY KEY DEFAULT true,
        policy text, epoch bigint NOT NULL DEFAULT 0,
        kind_index integer NOT NULL DEFAULT 0, shard integer, cursor uuid,
        complete boolean NOT NULL DEFAULT false,
        cleanup_pending boolean NOT NULL DEFAULT false,
        CONSTRAINT ck_analytics_card_timer_singleton CHECK(singleton))""")
    op.execute("INSERT INTO analytics_card_timer_bootstrap(singleton) VALUES(true)")


def downgrade():
    raise RuntimeError(
        "Native analytics requires a coordinated backup restore or forward repair; "
        "schema downgrade is not supported"
    )
