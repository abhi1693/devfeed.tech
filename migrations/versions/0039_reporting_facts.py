"""Capture bounded asynchronous report facts without scanning history in DDL."""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

JOBS = {
    "article_analysis_jobs": "article_analysis",
    "topic_analysis_jobs": "topic_analysis",
    "research_verification_jobs": "research_verification",
    "ingestion_jobs": "ingestion",
}
TABLES = (*JOBS, "articles", "article_reviews", "article_origins", "article_topics")
METRICS = ("count", "automatic", "tokens", "reported", "unreported", "duration_ms")


def upgrade():
    op.create_table(
        "reporting_fact_states",
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(40), primary_key=True),
        sa.Column("cursor_id", sa.Uuid()),
        sa.Column("complete", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "reporting_dirty",
        sa.Column("kind", sa.String(40), primary_key=True),
        sa.Column("entity_id", sa.Uuid(), primary_key=True),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_reporting_dirty_oldest", "reporting_dirty", ["created_at", "kind", "entity_id"]
    )
    op.create_table(
        "reporting_job_facts",
        sa.Column("kind", sa.String(40), primary_key=True),
        sa.Column("entity_id", sa.Uuid(), primary_key=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tokens", sa.Numeric()),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.Uuid()),
    )
    op.create_index("ix_reporting_job_facts_finished_at", "reporting_job_facts", ["finished_at"])
    op.create_index("ix_reporting_job_facts_window", "reporting_job_facts", ["kind", "finished_at"])
    op.create_table(
        "reporting_article_facts",
        sa.Column("content_type", sa.String(30), nullable=False),
        sa.Column("article_id", sa.Uuid(), primary_key=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("published", sa.Boolean(), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("latency_seconds", sa.Numeric()),
    )
    for column in ("discovered_at", "published_at"):
        op.create_index(f"ix_reporting_article_facts_{column}", "reporting_article_facts", [column])
    for suffix, dimension in (("sources", "source_id"), ("topics", "topic_id")):
        table = "reporting_article_" + suffix
        op.create_table(
            table,
            sa.Column("article_id", sa.Uuid(), primary_key=True),
            sa.Column(dimension, sa.Uuid(), primary_key=True),
        )
        op.create_index(f"ix_{table}_{suffix[:-1]}", table, [dimension, "article_id"])
    op.create_table(
        "reporting_daily_facts",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("kind", sa.String(40), primary_key=True),
        sa.Column("dimension_id", sa.Uuid(), primary_key=True),
        *[sa.Column(name, sa.Numeric(), nullable=False, server_default="0") for name in METRICS],
    )
    op.create_index("ix_reporting_daily_facts_kind_day", "reporting_daily_facts", ["kind", "day"])
    op.execute("""CREATE FUNCTION reporting_enqueue(p_kind text, p_id uuid) RETURNS void
        LANGUAGE sql AS $$
          INSERT INTO reporting_dirty(kind, entity_id) VALUES(p_kind,p_id)
          ON CONFLICT(kind,entity_id) DO UPDATE
          SET generation=reporting_dirty.generation+1;
        $$""")
    # No historical reads or contribution calculation occur inside writer transactions.
    op.execute("""CREATE FUNCTION reporting_capture() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE previous_id uuid; current_id uuid;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            IF TG_ARGV[1]='article_id' THEN previous_id:=OLD.article_id;
            ELSE previous_id:=OLD.id; END IF;
          END IF;
          IF TG_OP <> 'DELETE' THEN
            IF TG_ARGV[1]='article_id' THEN current_id:=NEW.article_id;
            ELSE current_id:=NEW.id; END IF;
          END IF;
          IF previous_id IS NOT NULL THEN PERFORM reporting_enqueue(TG_ARGV[0],previous_id); END IF;
          IF current_id IS NOT NULL AND current_id IS DISTINCT FROM previous_id THEN
            PERFORM reporting_enqueue(TG_ARGV[0],current_id);
          END IF;
          RETURN NULL;
        END $$""")
    for table in TABLES:
        kind = JOBS.get(table, "article")
        key = (
            "article_id"
            if table in ("article_reviews", "article_origins", "article_topics")
            else "id"
        )
        columns = (
            "id, status, finished_at, source_id"
            if table == "ingestion_jobs"
            else "id, status, finished_at, duration_ms, usage"
            if table in JOBS
            else "discovered_at, published_to_feed_at, publication_status, content_type"
            if table == "articles"
            else "article_id, action, automation, created_at"
            if table == "article_reviews"
            else "article_id, source_id"
            if table == "article_origins"
            else "article_id, topic_id, role"
        )
        for event in ("INSERT", "DELETE", "UPDATE"):
            change = f"UPDATE OF {columns}" if event == "UPDATE" else event
            condition = ""
            if table in JOBS:
                # Facts retain only finished terminal contributions. Bookkeeping
                # for an ineligible job cannot add, remove or change those facts.
                # Keep OLD eligibility so retrying a failed run removes its fact.
                statuses = "'failed'" if table == "ingestion_jobs" else "'succeeded','failed'"
                previous = f"(OLD.status IN ({statuses}) AND OLD.finished_at IS NOT NULL)"
                current = f"(NEW.status IN ({statuses}) AND NEW.finished_at IS NOT NULL)"
                if event == "UPDATE":
                    changes = [f"{previous} IS DISTINCT FROM {current}"]
                    changes.extend(
                        f"OLD.{name} IS DISTINCT FROM NEW.{name}"
                        for name in columns.split(", ")
                        if name not in {"status", "usage"}
                    )
                    if table != "ingestion_jobs":
                        changes.append(
                            "OLD.usage->'totalTokens' IS DISTINCT FROM NEW.usage->'totalTokens'"
                        )
                    condition = (
                        f" WHEN (({previous} OR {current}) AND (" + " OR ".join(changes) + "))"
                    )
                else:
                    condition = f" WHEN ({current if event == 'INSERT' else previous})"
            elif event == "UPDATE":
                names = columns.split(", ")
                condition = (
                    " WHEN ("
                    + " OR ".join(f"OLD.{c} IS DISTINCT FROM NEW.{c}" for c in names)
                    + ")"
                )
            op.execute(f"""CREATE TRIGGER reporting_{event.lower()} AFTER {change} ON {table}
                FOR EACH ROW{condition} EXECUTE FUNCTION reporting_capture('{kind}','{key}')""")
    # Administrative TRUNCATE bypasses row capture. Explicitly invalidate all
    # coverage and derived contributions; ordinary deletes retain bounded replay.
    op.execute("""CREATE FUNCTION reporting_reset() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          DELETE FROM reporting_daily_facts;
          DELETE FROM reporting_article_sources;
          DELETE FROM reporting_article_topics;
          DELETE FROM reporting_article_facts;
          DELETE FROM reporting_job_facts;
          DELETE FROM reporting_dirty;
          UPDATE reporting_fact_states SET cursor_id=NULL, complete=false, updated_at=now();
          RETURN NULL;
        END $$""")
    for table in TABLES:
        op.execute(f"""CREATE TRIGGER reporting_truncate AFTER TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION reporting_reset()""")
    # Every kind bootstraps by primary-key pages after deployment, under normal duty budgets.
    for kind in ("article", *JOBS.values()):
        op.execute(
            sa.text("INSERT INTO reporting_fact_states(kind) VALUES(:kind)").bindparams(kind=kind)
        )


def downgrade():
    for table in TABLES:
        for event in ("insert", "delete", "update", "truncate"):
            op.execute(f"DROP TRIGGER reporting_{event} ON {table}")
    op.execute("DROP FUNCTION reporting_reset()")
    op.execute("DROP FUNCTION reporting_capture()")
    op.execute("DROP FUNCTION reporting_enqueue(text,uuid)")
    for table in (
        "reporting_daily_facts",
        "reporting_article_topics",
        "reporting_article_sources",
        "reporting_article_facts",
        "reporting_job_facts",
        "reporting_dirty",
        "reporting_fact_states",
    ):
        op.drop_table(table)
