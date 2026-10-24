"""Compare retention on disposable migrated PostgreSQL; never use a production URL.

Run once per size on a dedicated PostgreSQL 17 instance. Results include exact
SQL plans and a concurrent ingestion writer. The synthetic clock advances only
maintenance cadence; it does not change retention age or write scheduling.
"""

import argparse
import importlib.util
import json
import os
import platform
import random
import statistics
import string
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from devfeed_core import job_retention
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleOrigin,
    IngestionJob,
    Source,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
)
from sqlalchemy import create_engine, event, func, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
ANCHOR = datetime(2026, 10, 24, 12, tzinfo=UTC)
SOURCE_ID = uuid.UUID("00000000-0000-0000-0000-000000000298")


def generated(connection, model, columns, source, parameters):
    statement = select(*(text(value) for value in columns.values()))
    connection.execute(
        insert(model.__table__).from_select(list(columns), statement.select_from(text(source))),
        parameters,
    )


def seed(engine, articles, payload_bytes, *, retained_success=True):
    """1.7 jobs/subject, including a six-run tail and both topic subject types."""
    rng = random.Random(298)
    payload = "".join(rng.choices(string.ascii_letters + string.digits, k=payload_bytes))
    parameters = {"articles": articles, "payload": payload, "anchor": ANCHOR}
    with engine.begin() as connection:
        # Seed only: suppress projection/notification triggers, restoring all triggers at commit.
        # Measured retention and ingestion execute the real migrated triggers/indexes.
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        tables = (
            connection.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
            .scalars()
            .all()
        )
        names = ", ".join('"' + name.replace('"', '""') + '"' for name in tables)
        connection.execute(text(f"TRUNCATE {names} CASCADE"))
        success_indexes(connection, retained_success)
        reset_maintenance(connection)
        connection.execute(
            insert(Source.__table__),
            [
                {
                    "id": SOURCE_ID,
                    "name": "Retention benchmark",
                    "slug": "retention-benchmark",
                    "source_type": "publisher",
                    "approval_status": "approved",
                    "feed_url": "https://retention.invalid/feed",
                }
            ],
        )
        generated(
            connection,
            Article,
            {
                "id": "md5('article-' || i)::uuid",
                "canonical_url": "'https://retention.invalid/' || i",
                "url_hash": "md5('article-' || i)",
                "title": "'Article ' || i",
                "slug": "'article-' || i",
                "summary": "'Representative retention fixture'",
                "content_type": "'article'",
                "review_status": "'approved'",
                "publication_status": "'published'",
                "feed_at": "CAST(:anchor AS timestamptz)",
                "discovered_at": "CAST(:anchor AS timestamptz) - interval '60 days'",
            },
            "generate_series(1, :articles) AS g(i)",
            parameters,
        )
        generated(
            connection,
            Topic,
            {
                "id": "md5('topic-' || i)::uuid",
                "name": "'Topic ' || i",
                "slug": "'topic-' || i",
                "kind": "'technology'",
                "status": "'active'",
            },
            "generate_series(1, :articles / 2) AS g(i)",
            parameters,
        )
        generated(
            connection,
            TopicProposal,
            {
                "id": "md5('proposal-' || i)::uuid",
                "batch_id": "md5('batch')::uuid",
                "slug": "'proposal-' || i",
                "action": "'create'",
                "origin": "'import'",
                "source_name": "'benchmark'",
                "proposed": "'{}'::jsonb",
                "created_by": "'{}'::jsonb",
                "status": "'rejected'",
                "reviewed_at": "CAST(:anchor AS timestamptz)",
                "reviewed_by": "'{}'::jsonb",
            },
            "generate_series(1, :articles / 2) AS g(i)",
            parameters,
        )
        history = (
            "generate_series(1, :articles) AS g(i) CROSS JOIN LATERAL "
            "generate_series(1, CASE WHEN i % 10 = 0 THEN 6 "
            "WHEN i % 10 IN (1, 2) THEN 2 ELSE 1 END) AS h(run)"
        )
        for model, prefix in (
            (ArticleAnalysisJob, "article-job-"),
            (TopicAnalysisJob, "topic-job-"),
        ):
            columns = {
                "id": f"md5('{prefix}' || i || '-' || run)::uuid",
                "input_snapshot": "jsonb_build_object('content', CAST(:payload AS text))",
                "status": "CASE WHEN i % 50 = 0 AND run = 1 THEN 'failed' "
                "WHEN i % 40 = 0 AND run = 6 THEN 'running' "
                "WHEN i % 20 = 0 AND run = 6 THEN 'queued' ELSE 'succeeded' END",
                "attempts": "1",
                "available_at": "CAST(:anchor AS timestamptz) - interval '60 days'",
                "created_at": "CAST(:anchor AS timestamptz) - interval '50 days' "
                "+ (i % 10000) * interval '1 second' + run * interval '1 hour'",
                "finished_at": "CASE WHEN i % 20 = 0 AND run = 6 THEN NULL ELSE "
                "CAST(:anchor AS timestamptz) - interval '40 days' "
                "+ (i % 10000) * interval '1 second' + run * interval '1 hour' END",
                "lease_until": "CASE WHEN i % 40 = 0 AND run = 6 THEN "
                "CAST(:anchor AS timestamptz) + interval '1 hour' END",
                "lease_token": "CASE WHEN i % 40 = 0 AND run = 6 THEN md5('lease-' || i)::uuid END",
                "result": '\'{"outcome": "ready"}\'::jsonb',
                "usage": '\'{"article_steps": {"overview": {"status": "succeeded", '
                '"output": {"summary": "retained source"}}}}\'::jsonb',
            }
            if model is ArticleAnalysisJob:
                columns.update(
                    article_id="md5('article-' || i)::uuid",
                    catalog_snapshot="jsonb_build_object('catalog', CAST(:payload AS text))",
                )
            else:
                columns.update(
                    proposal_id="CASE WHEN i % 2 = 0 THEN md5('proposal-' || i / 2)::uuid END",
                    topic_id="CASE WHEN i % 2 = 1 THEN md5('topic-' || (i + 1) / 2)::uuid END",
                    input_hash="md5(i::text)",
                    requested_by="'{}'::jsonb",
                    prompt_version="'bench'",
                )
            generated(connection, model, columns, history, parameters)
            tail = dict(columns)
            tail.update(
                id=f"md5('{prefix}failed-tail-' || run)::uuid",
                status="'failed'",
                lease_until="NULL",
                lease_token="NULL",
                created_at="CAST(:anchor AS timestamptz) - interval '45 days' "
                "+ run * interval '1 second'",
                finished_at="CAST(:anchor AS timestamptz) - interval '40 days' "
                "+ run * interval '1 second'",
            )
            generated(
                connection,
                model,
                tail,
                "(VALUES (1)) AS g(i) CROSS JOIN generate_series(1,2000) AS h(run)",
                parameters,
            )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM (ANALYZE)"))


def reset_maintenance(connection):
    if connection.execute(text("SELECT to_regclass('job_payload_retention')")).scalar():
        connection.execute(text("TRUNCATE job_payload_retention"))
        connection.execute(
            text(
                "INSERT INTO job_payload_retention(kind,next_run_at) "
                "VALUES ('article',:anchor),('topic',:anchor)"
            ),
            {"anchor": ANCHOR},
        )


def success_indexes(connection, enabled):
    for table, subject in (
        ("article_analysis_jobs", "article_id"),
        ("topic_analysis_jobs", "proposal_id"),
        ("topic_analysis_jobs", "topic_id"),
    ):
        name = f"ix_{table}_{subject}_retained_success"
        connection.execute(text(f"DROP INDEX IF EXISTS {name}"))
        if enabled:
            connection.execute(
                text(
                    f"CREATE INDEX {name} ON {table} ({subject},created_at,id) "
                    "WHERE status='succeeded' AND inputs_pruned_at IS NULL "
                    f"AND {subject} IS NOT NULL"
                )
            )


def latest_only(engine):
    with engine.begin() as connection:
        for table, subject in (
            ("article_analysis_jobs", "article_id"),
            ("topic_analysis_jobs", "COALESCE(proposal_id, topic_id)"),
        ):
            connection.execute(
                text(f"""
                WITH ranked AS (SELECT id, row_number() OVER (
                  PARTITION BY {subject} ORDER BY created_at DESC,id DESC) AS position
                  FROM {table} WHERE status='succeeded')
                UPDATE {table} j SET inputs_pruned_at=:anchor,input_snapshot='{{}}'::jsonb
                FROM ranked WHERE ranked.id=j.id AND ranked.position > 1
            """),
                {"anchor": ANCHOR},
            )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM (ANALYZE)"))


def seed_cadence_backlog(engine, subjects, payload_bytes):
    rng = random.Random(298)
    payload = "".join(rng.choices(string.ascii_letters + string.digits, k=payload_bytes))
    parameters = {"subjects": min(subjects, 1000), "payload": payload, "anchor": ANCHOR}
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        for model, prefix in ((ArticleAnalysisJob, "article"), (TopicAnalysisJob, "topic")):
            columns = {
                "id": f"md5('{prefix}-cadence-' || i || '-' || run)::uuid",
                "input_snapshot": "jsonb_build_object('content', CAST(:payload AS text))",
                "status": "'succeeded'",
                "attempts": "1",
                "available_at": "CAST(:anchor AS timestamptz) - interval '100 days'",
                "created_at": "CAST(:anchor AS timestamptz) - interval '100 days' "
                "+ run * interval '1 hour'",
                "finished_at": "CAST(:anchor AS timestamptz) - interval '90 days' "
                "+ run * interval '1 hour'",
            }
            if model is ArticleAnalysisJob:
                columns.update(
                    article_id="md5('article-' || i)::uuid",
                    catalog_snapshot="jsonb_build_object('catalog', CAST(:payload AS text))",
                )
            else:
                columns.update(
                    proposal_id="CASE WHEN i % 2 = 0 THEN md5('proposal-' || i / 2)::uuid END",
                    topic_id="CASE WHEN i % 2 = 1 THEN md5('topic-' || (i + 1) / 2)::uuid END",
                    input_hash="md5(i::text)",
                    requested_by="'{}'::jsonb",
                    prompt_version="'cadence'",
                )
            generated(
                connection,
                model,
                columns,
                "generate_series(1, :subjects) AS g(i) CROSS JOIN generate_series(1,2) AS h(run)",
                parameters,
            )
        reset_maintenance(connection)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM (ANALYZE)"))


class Ingestion:
    """Twenty committed article/origin/outbox writes per second, no external I/O."""

    def __init__(self, factory):
        self.factory = factory
        self.stop = threading.Event()
        self.samples = []
        self.errors = []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            started = time.perf_counter()
            try:
                article_id = uuid.uuid4()
                with self.factory.begin() as session:
                    session.execute(
                        insert(Article.__table__),
                        [
                            {
                                "id": article_id,
                                "canonical_url": f"https://ingestion.invalid/{article_id}",
                                "url_hash": article_id.hex,
                                "title": "Concurrent ingestion",
                                "slug": f"ingestion-{article_id.hex}",
                            }
                        ],
                    )
                    session.execute(
                        insert(ArticleOrigin.__table__),
                        [
                            {
                                "article_id": article_id,
                                "source_id": SOURCE_ID,
                                "entry_key": article_id.hex,
                                "original_url": f"https://ingestion.invalid/{article_id}",
                            }
                        ],
                    )
                    session.execute(
                        insert(IngestionJob.__table__),
                        [
                            {
                                "source_id": SOURCE_ID,
                                "status": "succeeded",
                                "entries_seen": 1,
                                "articles_created": 1,
                                "finished_at": ANCHOR,
                            }
                        ],
                    )
                    session.execute(
                        insert(ArticleAnalysisJob.__table__),
                        [
                            {
                                "article_id": article_id,
                                "status": "queued",
                                "input_snapshot": {"text": "Concurrent ingestion payload"},
                            }
                        ],
                    )
                self.samples.append((time.perf_counter() - started) * 1000)
            except Exception as exc:
                self.errors.append(type(exc).__name__)
                self.stop.set()
            self.stop.wait(max(0, 0.05 - (time.perf_counter() - started)))


def distribution(values):
    ordered = sorted(values)
    return {
        "calls": len(ordered),
        "median_ms": statistics.median(ordered) if ordered else None,
        "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else None,
        "p99_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))] if ordered else None,
        "max_ms": max(ordered) if ordered else None,
    }


def checkpointed(module):
    return hasattr(module, "JobPayloadRetention")


def examined_job_rows(node):
    own = 0
    if "analysis_jobs" in node.get("Relation Name", ""):
        own = (
            node.get("Actual Rows", 0)
            + node.get("Rows Removed by Filter", 0)
            + node.get("Rows Removed by Index Recheck", 0)
        ) * node.get("Actual Loops", 1)
    return own + sum(examined_job_rows(child) for child in node.get("Plans", []))


def collect_plans(engine, module, *, deep_cursor=False):
    """Replay one real page under an outer rollback, explaining before each SELECT."""
    plans, seen = [], set()
    explaining = False
    with engine.connect() as connection:
        transaction = connection.begin()
        plan_factory = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")
        if deep_cursor and checkpointed(module):
            cutoff = ANCHOR - timedelta(days=get_settings().job_payload_retention_days)
            for kind, model in (("article", ArticleAnalysisJob), ("topic", TopicAnalysisJob)):
                conditions = (
                    model.status == "succeeded",
                    model.inputs_pruned_at.is_(None),
                    model.finished_at < cutoff,
                )
                count = connection.scalar(
                    select(func.count()).select_from(model).where(*conditions)
                )
                boundary = connection.execute(
                    select(model.finished_at, model.id)
                    .where(*conditions)
                    .order_by(model.finished_at, model.id)
                    .offset(max(0, count * 4 // 5 - 1))
                    .limit(1)
                ).first()
                if boundary is not None:
                    connection.execute(
                        update(module.JobPayloadRetention)
                        .where(module.JobPayloadRetention.kind == kind)
                        .values(
                            next_run_at=ANCHOR,
                            cutoff=cutoff,
                            cursor_finished_at=boundary.finished_at,
                            cursor_id=boundary.id,
                        )
                    )

        def capture(conn, cursor, statement, parameters, context, executemany):
            nonlocal explaining
            if (
                explaining
                or not statement.lstrip().startswith("SELECT")
                or "analysis_jobs" not in statement
                or statement in seen
                or len(seen) >= 10
            ):
                return
            seen.add(statement)
            explaining = True
            try:
                plan = connection.exec_driver_sql(
                    "EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON) " + statement,
                    parameters,
                ).scalar()[0]
                examined = examined_job_rows(plan["Plan"])
                if (
                    checkpointed(module)
                    and examined > get_settings().job_payload_scan_batch_size * 12
                ):
                    raise RuntimeError(
                        f"Candidate plan examined excessive job-history rows: {examined}"
                    )
                plans.append({"sql": statement, "plan": plan, "examined_job_rows": examined})
            finally:
                explaining = False

        event.listen(engine, "before_cursor_execute", capture)
        try:
            with patch.object(module, "utcnow", return_value=ANCHOR):
                module.prune_job_payloads(plan_factory)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
            transaction.rollback()
    return plans


def checkpointer(connection):
    return dict(
        connection.execute(
            text(
                "SELECT num_timed,num_requested,write_time,sync_time,"
                "buffers_written FROM pg_stat_checkpointer"
            )
        )
        .mappings()
        .one()
    )


def wal_stats(connection):
    return {
        key: int(value)
        for key, value in connection.execute(
            text("SELECT wal_records,wal_fpi,wal_bytes,wal_buffers_full FROM pg_stat_wal")
        )
        .mappings()
        .one()
        .items()
    }


def measure(
    engine,
    factory,
    module,
    calls,
    *,
    clock_step,
    concurrent,
    converge=False,
    deep=False,
    tick_interval=0,
):
    plans = collect_plans(engine, module)
    deep_plans = (
        collect_plans(engine, module, deep_cursor=True) if deep and checkpointed(module) else []
    )
    # Both implementations start each timed phase at the same checkpoint boundary.
    # Preparation and rolled-back plan passes cannot leave a pending checkpoint.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        forced_checkpoint_before = checkpointer(connection)
        connection.execute(text("CHECKPOINT"))
        forced_checkpoint_after = checkpointer(connection)
    statement_count = 0
    owner = threading.get_ident()
    backend_pids = {}
    wait_samples, observer_errors = [], []
    observer_stop = threading.Event()

    def observe():
        try:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                while not observer_stop.wait(0.1):
                    rows = (
                        connection.execute(
                            text(
                                "SELECT pid,state,wait_event_type,wait_event,"
                                "pg_blocking_pids(pid) AS blockers "
                                "FROM pg_stat_activity WHERE pid=ANY(:pids)"
                            ),
                            {"pids": list(backend_pids)},
                        )
                        .mappings()
                        .all()
                    )
                    for row in rows:
                        wait_samples.append(
                            {
                                "elapsed_seconds": time.perf_counter() - wall_started,
                                "role": backend_pids.get(row["pid"]),
                                **dict(row),
                            }
                        )
        except Exception as exc:
            observer_errors.append(type(exc).__name__)

    observer = threading.Thread(target=observe, daemon=True)

    def capture(conn, cursor, statement, parameters, context, executemany):
        nonlocal statement_count
        thread_id = threading.get_ident()
        if thread_id == owner or thread_id == writer.thread.ident:
            backend_pids[conn.connection.driver_connection.info.backend_pid] = (
                "retention" if thread_id == owner else "ingestion"
            )
        if thread_id == owner and not context.execution_options.get("benchmark_probe"):
            statement_count += 1

    writer = Ingestion(factory)
    with engine.connect() as connection:
        before_lsn = connection.execute(text("SELECT pg_current_wal_insert_lsn()::text")).scalar()
        checkpoints_before = checkpointer(connection)
        wal_before = wal_stats(connection)
    event.listen(engine, "before_cursor_execute", capture)
    durations, pruned = [], []
    cpu_started, wall_started = time.process_time(), time.perf_counter()
    observer.start()
    if concurrent:
        writer.thread.start()
    try:
        for index in range(calls):
            with patch.object(
                module, "utcnow", return_value=ANCHOR + timedelta(seconds=index * clock_step)
            ):
                started = time.perf_counter()
                pruned.append(module.prune_job_payloads(factory))
                durations.append((time.perf_counter() - started) * 1000)
                if concurrent:
                    delay = (
                        (index + 1) * tick_interval - (time.perf_counter() - wall_started)
                        if tick_interval
                        else 0.05 - (time.perf_counter() - started)
                    )
                    writer.stop.wait(max(0, delay))
                if converge:
                    if checkpointed(module):
                        with engine.connect().execution_options(benchmark_probe=True) as connection:
                            complete = connection.execute(
                                text("SELECT bool_and(cutoff IS NULL) FROM job_payload_retention")
                            ).scalar()
                    else:
                        complete = pruned[-1] == 0
                    if complete:
                        break
        else:
            if converge:
                raise RuntimeError("Backlog did not converge within bounded benchmark page budget")
    finally:
        if concurrent:
            writer.stop.set()
            writer.thread.join(timeout=10)
        observer_stop.set()
        observer.join(timeout=10)
        event.remove(engine, "before_cursor_execute", capture)
    wall_seconds, cpu_seconds = (
        time.perf_counter() - wall_started,
        time.process_time() - cpu_started,
    )
    with engine.connect() as connection:
        wal = connection.execute(
            text("SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(), :lsn)"), {"lsn": before_lsn}
        ).scalar()
        checkpoints_after = checkpointer(connection)
        wal_after = wal_stats(connection)
    cap = get_settings().job_payload_prune_batch_size * 2
    if max(pruned, default=0) > cap:
        raise RuntimeError("Retention exceeded the configured prune bound")
    if checkpointed(module) and clock_step == 0 and plans:
        raise RuntimeError(
            "An ordinary tick accessed analysis-job history before maintenance was due"
        )
    if checkpointed(module) and converge:
        with engine.connect() as connection:
            for model in (ArticleAnalysisJob, TopicAnalysisJob):
                remaining = connection.scalar(
                    select(model.id)
                    .where(
                        model.finished_at < ANCHOR - timedelta(days=30),
                        *module._eligible_conditions(model),
                    )
                    .limit(1)
                )
                if remaining is not None:
                    raise RuntimeError("A completed sweep left an eligible payload behind")
    if observer_errors:
        raise RuntimeError(f"Wait sampling failed: {observer_errors}")
    if writer.errors:
        raise RuntimeError(f"Concurrent ingestion failed: {writer.errors}")
    return {
        "retention": distribution(durations),
        "pruned": sum(pruned),
        "max_pruned_per_call": max(pruned, default=0),
        "application_cpu_seconds_including_ingestion": cpu_seconds,
        "retention_runtime_seconds": sum(durations) / 1000,
        "wall_seconds": wall_seconds,
        "sql_statements": statement_count,
        "wal_bytes_including_ingestion": int(wal),
        "ingestion": distribution(writer.samples),
        "plans": plans,
        "deep_cursor_plans": deep_plans,
        "retention_samples_ms": durations,
        "ingestion_samples_ms": writer.samples,
        "checkpointer_before": checkpoints_before,
        "checkpointer_after": checkpoints_after,
        "forced_checkpoint_before": forced_checkpoint_before,
        "forced_checkpoint_after": forced_checkpoint_after,
        "wal_counters_before": wal_before,
        "wal_counters_after": wal_after,
        "wal_counters_delta": {key: wal_after[key] - wal_before[key] for key in wal_before},
        "wait_samples": wait_samples,
        "clock_step_seconds": clock_step,
        "real_tick_interval_seconds": tick_interval,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--articles", type=int, required=True)
    parser.add_argument("--baseline-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backlog-pages", type=int, default=50)
    parser.add_argument("--payload-bytes", type=int, default=4096)
    parser.add_argument("--cadence-interval-seconds", type=float, default=15)
    args = parser.parse_args()
    if args.cadence_interval_seconds <= 0:
        parser.error("--cadence-interval-seconds must be positive")
    url = os.environ["DEVFEED_TEST_DATABASE_URL"]
    if not (make_url(url).database or "").endswith("_test"):
        parser.error("DEVFEED_TEST_DATABASE_URL must name a dedicated disposable _test database")
    os.environ["DEVFEED_DATABASE_URL"] = url
    os.environ.setdefault("DEVFEED_REDIS_URL", "redis://127.0.0.1/15")
    for key, value in {
        "RETENTION_DAYS": "30",
        "PRUNE_BATCH_SIZE": "100",
        "SCAN_BATCH_SIZE": "1000",
        "PAGE_INTERVAL_SECONDS": "30",
        "SWEEP_INTERVAL_SECONDS": "3600",
    }.items():
        os.environ[f"DEVFEED_JOB_PAYLOAD_{key}"] = value
    get_settings.cache_clear()
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    engine = create_engine(url, pool_size=4, max_overflow=0)
    factory = sessionmaker(engine, expire_on_commit=False)
    specification = importlib.util.spec_from_file_location(
        "baseline_job_retention", args.baseline_file
    )
    baseline = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(baseline)
    with engine.connect() as connection:
        version = connection.execute(text("SELECT version()")).scalar()
        settings = dict(
            connection.execute(
                text(
                    "SELECT name,setting FROM pg_settings WHERE name IN "
                    "('shared_buffers','work_mem','max_wal_size','synchronous_commit','track_io_timing')"
                )
            ).all()
        )
    if "PostgreSQL 17." not in version:
        parser.error("Use PostgreSQL 17 for the production comparison")
    result = {
        "articles": args.articles,
        "jobs_per_kind": args.articles * 17 // 10 + 2000,
        "payload_bytes": args.payload_bytes,
        "application_architecture": platform.machine(),
        "postgres_version": version,
        "postgres_settings": settings,
        "cpu_count": os.cpu_count(),
        "phases": {},
    }
    for name, module in (("baseline", baseline), ("current", job_retention)):
        print(f"Seeding {name}: {args.articles} articles", flush=True)
        seed(engine, args.articles, args.payload_bytes, retained_success=checkpointed(module))
        result["phases"][f"{name}_backlog"] = measure(
            engine, factory, module, args.backlog_pages, clock_step=31, concurrent=True, deep=True
        )
        os.environ["DEVFEED_JOB_PAYLOAD_PRUNE_BATCH_SIZE"] = "1000"
        get_settings.cache_clear()
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE job_payload_retention SET next_run_at=:anchor"), {"anchor": ANCHOR}
            )
        result["phases"][f"{name}_convergence_prune1000_scan1000"] = measure(
            engine,
            factory,
            module,
            args.articles // 100 + 100,
            clock_step=31,
            concurrent=True,
            converge=True,
        )
        os.environ["DEVFEED_JOB_PAYLOAD_PRUNE_BATCH_SIZE"] = "100"
        get_settings.cache_clear()
        latest_only(engine)
        # Clear maintenance state between independent scenarios in this disposable DB.
        with engine.begin() as connection:
            reset_maintenance(connection)
        result["phases"][f"{name}_latest_only"] = measure(
            engine, factory, module, 5, clock_step=31, concurrent=True, deep=True
        )
        result["phases"][f"{name}_ordinary_ticks"] = measure(
            engine, factory, module, 20, clock_step=0, concurrent=True
        )
        result["phases"][f"{name}_ordinary_ticks_no_writer"] = measure(
            engine, factory, module, 20, clock_step=0, concurrent=False
        )
        seed_cadence_backlog(engine, args.articles, args.payload_bytes)
        result["phases"][f"{name}_deployed_cadence"] = measure(
            engine,
            factory,
            module,
            5,
            clock_step=15,
            concurrent=True,
            tick_interval=args.cadence_interval_seconds,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, default=str) + "\n")
        for phase, data in result["phases"].items():
            print(
                phase,
                {
                    key: value
                    for key, value in data.items()
                    if key
                    not in {
                        "plans",
                        "deep_cursor_plans",
                        "retention_samples_ms",
                        "ingestion_samples_ms",
                        "wait_samples",
                    }
                },
                flush=True,
            )
    engine.dispose()


if __name__ == "__main__":
    main()
