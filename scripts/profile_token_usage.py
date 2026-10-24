"""Compare the original token report and current application SQL on disposable PostgreSQL.

Requires a migrated, explicitly local DEVFEED_TEST_DATABASE_URL ending in _test.
--seed replaces articles and topic proposals, including their dependent job histories.
Never point this tool at shared development data. Raw EXPLAIN plans accompany the report.
"""

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from devfeed_admin_api.automation import analysis_token_activity
from devfeed_core.models import ArticleAnalysisJob, ResearchVerificationJob, TopicAnalysisJob
from sqlalchemy import BigInteger, case, cast, create_engine, func, literal, select, text, union_all
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
from sqlalchemy.engine import make_url

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
MODELS = (
    ("article_analysis", ArticleAnalysisJob),
    ("topic_analysis", TopicAnalysisJob),
    ("research_verification", ResearchVerificationJob),
)


def original_statement(start, end):
    """Frozen pre-#295 SQL: unique completion timestamps cross the CTE boundary."""
    statements = []
    for kind, model in MODELS:
        usage = (
            select(
                model.finished_at,
                model.duration_ms,
                func.left(model.usage["totalTokens"].astext, 19).label("raw_tokens"),
            )
            .where(
                model.finished_at >= start,
                model.finished_at <= end,
                model.status.in_(("succeeded", "failed")),
            )
            .cte(f"{kind}_usage")
            .prefix_with("MATERIALIZED")
        )
        day = func.date(func.timezone("UTC", usage.c.finished_at))
        tokens = case(
            (usage.c.raw_tokens.op("~")(r"^[0-9]{1,18}$"), cast(usage.c.raw_tokens, BigInteger)),
            else_=None,
        )
        statements.append(
            select(
                day.label("date"),
                literal(kind).label("kind"),
                func.coalesce(func.sum(tokens), 0).label("tokens"),
                func.count(tokens).label("reported"),
                (func.count() - func.count(tokens)).label("unreported"),
                func.coalesce(func.sum(usage.c.duration_ms), 0).label("duration"),
            ).group_by(day)
        )
    return union_all(*statements)


class Capture:
    """Obtain the exact production statement without running the reporting query."""

    def execute(self, statement):
        self.statement = statement
        return self

    def mappings(self):
        return ()


def current_statement(start, end):
    capture = Capture()
    analysis_token_activity(capture, start, end)
    return capture.statement


def prepared_sql(statement):
    # Retain every application bind, including statuses/UTC/regex/JSON keys.
    # EXPLAIN EXECUTE describes actual cached generic/custom parameterized SQL.
    compiled = statement.compile(
        dialect=PGDialect_psycopg(paramstyle="numeric_dollar"),
        compile_kwargs={"render_postcompile": True},
    )
    arguments = ",".join(
        str(
            literal(compiled.params[name]).compile(
                dialect=PGDialect_psycopg(), compile_kwargs={"literal_binds": True}
            )
        )
        for name in compiled.positiontup
    )
    return str(compiled), arguments


def configure(connection, mode):
    for setting in (
        "SET work_mem='4MB'",
        "SET jit=on",
        "SET max_parallel_workers_per_gather=2",
        "SET track_io_timing=on",
        "SET statement_timeout='90s'",
        "SET timezone='Asia/Kolkata'",
        f"SET plan_cache_mode='force_{mode}_plan'",
    ):
        connection.exec_driver_sql(setting)
    connection.commit()


def seed(engine, articles):
    """Actual migrated tables/indexes, skewed 180-day history and incompressible TOAST."""
    with engine.begin() as connection:
        # A pooled measurement connection retains its bounded report timeout.
        # Fixture construction has a separate budget and keeps all ordinary triggers.
        connection.exec_driver_sql("SET LOCAL statement_timeout='10min'")
        connection.exec_driver_sql("TRUNCATE articles,topic_proposals CASCADE")
        connection.execute(
            text("""INSERT INTO articles
            (id,canonical_url,url_hash,title,slug,summary,content_type,feed_at,discovered_at)
            SELECT md5('token-article-'||n)::uuid,'https://example.invalid/'||n,
                md5('token-article-'||n),'Token profile '||n,'token-profile-'||n,
                'Disposable token reporting fixture','article',:now,:now
            FROM generate_series(1,:articles) n"""),
            {"now": NOW, "articles": articles},
        )
        connection.execute(
            text("""INSERT INTO topic_proposals
            (id,batch_id,slug,action,origin,source_name,proposed,evidence,status,created_at,created_by)
            SELECT md5('token-proposal-'||n)::uuid,md5('token-profile')::uuid,
                'token-proposal-'||n,'create','import','Token profile','{}','[]','pending',:now,'{}'
            FROM generate_series(1,10000) n"""),
            {"now": NOW},
        )
        # Every fifth job has >4 KiB of incompressible provider/step telemetry.
        # Build a bounded pool once, rather than hide seed CPU in measured samples.
        connection.exec_driver_sql("""CREATE TEMP TABLE token_profile_payloads ON COMMIT DROP AS
            SELECT p,string_agg(md5(p::text||'-'||n::text),'') payload
            FROM generate_series(0,255) p CROSS JOIN generate_series(1,128) n GROUP BY p""")
        for kind, model in MODELS[:2]:
            article = kind == "article_analysis"
            target = "article_id" if article else "proposal_id"
            target_id = (
                "md5('token-article-'||(1+(n-1)%:articles))::uuid"
                if article
                else "md5('token-proposal-'||(1+(n-1)%10000))::uuid"
            )
            columns = (
                "input_snapshot,catalog_snapshot,result,prompt_version"
                if article
                else "input_hash,input_snapshot,requested_by,result,prompt_version"
            )
            values = (
                "'{}','{}','{}','profile'" if article else "repeat('a',64),'{}','{}','{}','profile'"
            )
            recent = 30 if article else 80
            connection.execute(
                text(f"""INSERT INTO {model.__tablename__}
                (id,{target},status,attempts,available_at,created_at,finished_at,duration_ms,usage,
                 {columns})
                SELECT md5('{kind}-'||n)::uuid,{target_id},
                    CASE WHEN n%13=0 THEN 'failed' ELSE 'succeeded' END,1,:now,:now,
                    :now-CASE WHEN n%100<{recent}
                        THEN ((n*7919::bigint)%2592000)*interval '1 second'
                        ELSE interval '30 days'
                            +((n*15485863::bigint)%12960000)*interval '1 second' END,
                    n%60000,
                    (CASE n%11
                        WHEN 0 THEN '{{}}'::jsonb
                        WHEN 1 THEN '{{"totalTokens": null}}'::jsonb
                        WHEN 2 THEN '{{"totalTokens":"broken"}}'::jsonb
                        WHEN 3 THEN '{{"totalTokens":-1}}'::jsonb
                        WHEN 4 THEN '{{"totalTokens": 1.5}}'::jsonb
                        WHEN 5 THEN '{{"totalTokens":"1234567890123456789"}}'::jsonb
                        WHEN 6 THEN '{{"totalTokens":"999999999999999999"}}'::jsonb
                        WHEN 7 THEN '{{"totalTokens": 0}}'::jsonb
                        WHEN 8 THEN jsonb_build_object('totalTokens',(n%100000)::text)
                        ELSE jsonb_build_object('totalTokens',n%100000) END)
                    || CASE WHEN n%5=0 THEN jsonb_build_object('provider_metadata',p.payload)
                       ELSE '{{}}'::jsonb END,{values}
                FROM generate_series(1,:jobs) n
                JOIN token_profile_payloads p ON p.p=n%256"""),
                {"now": NOW, "articles": articles, "jobs": 2 * articles},
            )
        connection.execute(
            text("""INSERT INTO research_verification_jobs
            (id,status,attempts,available_at,created_at,finished_at,relationships,usage,duration_ms)
            SELECT id,status,attempts,available_at,created_at,finished_at,false,usage,duration_ms
            FROM topic_analysis_jobs WHERE id IN (
                SELECT md5('topic_analysis-'||n)::uuid FROM generate_series(1,:research) n)
            """),
            {"research": max(1, articles * 3 // 1000)},
        )
        connection.execute(
            text("""INSERT INTO article_analysis_jobs
            (id,article_id,status,attempts,available_at,created_at,input_snapshot,catalog_snapshot,result)
            SELECT md5('unfinished-'||n)::uuid,md5('token-article-'||n)::uuid,
                CASE WHEN n%2=0 THEN 'running' ELSE 'queued' END,0,:now,:now,'{}','{}','{}'
            FROM generate_series(1,:articles) n WHERE n%97=0"""),
            {"now": NOW, "articles": articles},
        )
    # Commit before ANALYZE so every profiling connection sees identical statistics.
    with engine.begin() as connection:
        for table in ("articles", "topic_proposals", *(m.__tablename__ for _, m in MODELS)):
            connection.exec_driver_sql(f"ANALYZE {table}")


def relation_sizes(connection):
    return [
        dict(row)
        for row in connection.execute(
            text("""SELECT c.relname,c.reltuples::bigint estimated_rows,
                pg_relation_size(c.oid) heap_bytes,pg_total_relation_size(c.oid) total_bytes,
                CASE WHEN c.reltoastrelid=0 THEN 0
                    ELSE pg_total_relation_size(c.reltoastrelid) END toast_bytes
            FROM pg_class c WHERE c.oid IN ('articles'::regclass,
                'article_analysis_jobs'::regclass,'topic_analysis_jobs'::regclass,
                'research_verification_jobs'::regclass) ORDER BY c.relname""")
        ).mappings()
    ]


def history_counts(connection):
    histories = {}
    for kind, model in MODELS:
        row = (
            connection.execute(
                text(f"""SELECT count(*) total_rows,
            min(finished_at) oldest_completion,max(finished_at) newest_completion,
            count(DISTINCT finished_at) distinct_completions,
            count(*) FILTER (WHERE finished_at>=:now-interval '12 hours') one_day_rows,
            count(*) FILTER (WHERE finished_at>=:now-interval '6 days 12 hours') seven_day_rows,
            count(*) FILTER (WHERE finished_at>=:now-interval '29 days 12 hours') thirty_day_rows
            FROM {model.__tablename__} WHERE status IN ('succeeded','failed')"""),
                {"now": NOW},
            )
            .mappings()
            .one()
        )
        histories[kind] = dict(row)
    return histories


def postgres_cpu(container):
    """Cgroup CPU is actual database container CPU, not SQL elapsed time."""
    if not container:
        return None
    pid = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.State.Pid}}", container], text=True
    ).strip()
    for entry in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
        if entry.startswith("0::"):
            path = Path("/sys/fs/cgroup") / entry.split("::", 1)[1].lstrip("/") / "cpu.stat"
            return int(dict(line.split() for line in path.read_text().splitlines())["usage_usec"])
    raise RuntimeError("Database CPU measurement requires cgroup v2")


class Ingestion:
    def __init__(self, engine, rate):
        self.engine, self.rate = engine, rate
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.count, self.errors, self.samples = 0, [], []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            with self.engine.connect() as connection:
                while not self.stop.is_set():
                    started = time.perf_counter()
                    identifier = str(time.time_ns())
                    connection.execute(
                        text("""WITH article AS (
                            INSERT INTO articles
                            (id,canonical_url,url_hash,title,slug,summary,content_type,feed_at,discovered_at)
                            VALUES (gen_random_uuid(),'https://example.invalid/ingest/'||:n,
                            md5('ingest-'||:n),'Concurrent ingestion','ingest-'||:n,
                            '','article',:now,:now)
                            RETURNING id)
                        INSERT INTO article_analysis_jobs
                        (id,article_id,status,attempts,available_at,created_at,input_snapshot,
                         catalog_snapshot,result,usage)
                        SELECT md5('ingest-'||:n)::uuid,id,'running',1,:now,:now,'{}','{}','{}','{}'
                        FROM article"""),
                        {"n": identifier, "now": NOW + timedelta(days=1)},
                    )
                    connection.execute(
                        text("""UPDATE article_analysis_jobs
                        SET status='succeeded',finished_at=:now,
                        duration_ms=100,usage='{"totalTokens": 1000}'
                        WHERE id=md5('ingest-'||:n)::uuid"""),
                        {"n": identifier, "now": NOW + timedelta(days=1)},
                    )
                    connection.commit()
                    self.count += 1
                    self.samples.append((time.perf_counter() - started) * 1000)
                    self.ready.set()
                    self.stop.wait(max(0, 1 / self.rate - (time.perf_counter() - started)))
        except Exception as error:
            self.errors.append(str(error))
            self.ready.set()

    def __enter__(self):
        self.thread.start()
        assert self.ready.wait(15), "Concurrent ingestion did not start"
        assert not self.errors, self.errors
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=15)
        assert not self.thread.is_alive(), "Concurrent ingestion did not stop"
        assert not self.errors, self.errors


def nodes(plan):
    yield plan
    for child in plan.get("Plans", ()):
        yield from nodes(child)


def measure(engine, days, mode, repeats, container, rate):
    start = NOW.replace(hour=0) - timedelta(days=days - 1)
    results, plans = {}, {}
    samples: dict[str, list[dict[str, Any]]] = {}
    ingestion: dict[str, list[dict[str, Any]]] = {}
    with engine.connect() as connection:
        configure(connection, mode)
        statements = {}
        for name, builder in (("original", original_statement), ("current", current_statement)):
            query, arguments = prepared_sql(builder(start, NOW))
            connection.exec_driver_sql(f"PREPARE token_{name} AS {query}")
            statements[name] = f"EXECUTE token_{name}({arguments})"
        for repeat in range(repeats + 1):
            for name in ("original", "current") if repeat % 2 == 0 else ("current", "original"):
                execute = statements[name]
                # A separate writer for each strategy gives matched requested load.
                with Ingestion(engine, rate) as writer:
                    db_cpu = postgres_cpu(container)
                    writes_before = writer.count
                    app_cpu, started = time.thread_time(), time.perf_counter()
                    rows = sorted(tuple(row) for row in connection.exec_driver_sql(execute))
                    elapsed = (time.perf_counter() - started) * 1000
                    cpu = (time.thread_time() - app_cpu) * 1000
                    db_after = postgres_cpu(container)
                    overlapping_writes = writer.count - writes_before
                results[name] = rows
                if len(results) == 2:
                    assert results["original"] == results["current"], "Token accounting changed"
                if repeat:  # First pass warms both paths without dropping shared caches.
                    samples.setdefault(name, []).append(
                        {
                            "elapsed_ms": elapsed,
                            "application_cpu_ms": cpu,
                            "database_container_cpu_ms": None
                            if db_cpu is None
                            else (db_after - db_cpu) / 1000,
                        }
                    )
                    ingestion.setdefault(name, []).append(
                        {
                            "insert_and_finish": writer.count,
                            "completed_during_report": overlapping_writes,
                            "latency_ms": writer.samples,
                        }
                    )
                if repeat == repeats:
                    plans[name] = connection.exec_driver_sql(
                        "EXPLAIN (ANALYZE,BUFFERS,VERBOSE,SETTINGS,FORMAT JSON) " + execute
                    ).scalar_one()[0]
        connection.exec_driver_sql("DEALLOCATE ALL")
        connection.commit()
    summaries = {}
    for name in ("original", "current"):
        plan = plans[name]
        groups = [node for node in nodes(plan["Plan"]) if node["Node Type"] == "Aggregate"]
        summaries[name] = {
            "samples": samples[name],
            "median_ms": statistics.median(sample["elapsed_ms"] for sample in samples[name]),
            "ingestion": ingestion[name],
            "execution_ms": plan["Execution Time"],
            "buffers": {key: value for key, value in plan["Plan"].items() if "Blocks" in key},
            "aggregates": [
                {
                    key: node[key]
                    for key in (
                        "Node Type",
                        "Strategy",
                        "Plan Rows",
                        "Actual Rows",
                        "Peak Memory Usage",
                        "Disk Usage",
                        "HashAgg Batches",
                    )
                    if key in node
                }
                for node in groups
            ],
            "plan": plan,
        }
    original_groups = sum(g["Plan Rows"] for g in summaries["original"]["aggregates"])
    current_groups = sum(g["Plan Rows"] for g in summaries["current"]["aggregates"])
    accesses = {
        name: sum(
            summary["buffers"].get(key, 0) for key in ("Shared Hit Blocks", "Shared Read Blocks")
        )
        for name, summary in summaries.items()
    }
    checks = {
        "grouping_estimates_not_worse": current_groups <= original_groups,
        "grouping_estimates_bounded": all(
            g["Plan Rows"] <= 200 for g in summaries["current"]["aggregates"]
        ),
        "aggregate_disk_usage_zero": all(
            g.get("Disk Usage", 0) == 0 for g in summaries["current"]["aggregates"]
        ),
        "temporary_writes_zero": summaries["current"]["buffers"].get("Temp Written Blocks", 0) == 0,
        "no_sort_spills": all(
            node.get("Sort Space Type") != "Disk" for node in nodes(plans["current"]["Plan"])
        ),
        "buffer_accesses_not_amplified": accesses["current"] <= 1.05 * accesses["original"] + 16,
        "runtime_within_noise_allowance": summaries["current"]["median_ms"]
        <= max(1.2 * summaries["original"]["median_ms"], summaries["original"]["median_ms"] + 20),
    }
    canonical = json.dumps(results["current"], default=str, separators=(",", ":"))
    return {
        "days": days,
        "plan_mode": mode,
        "result_equality": True,
        "result_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "returned_groups": len(results["current"]),
        "checks": checks,
        "strategies": summaries,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--articles", nargs="+", type=int, default=[100000, 300000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--ingestion-rate", type=int, default=20)
    parser.add_argument("--postgres-container")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = make_url(os.environ["DEVFEED_TEST_DATABASE_URL"])
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"127.0.0.1", "localhost"}
        or not (url.database or "").endswith("_test")
        or url.query
        or any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"))
    ):
        parser.error("Use an explicitly disposable local database ending in _test")
    if not (
        all(100 <= count <= 1000000 for count in args.articles)
        and 1 <= args.repeats <= 10
        and 1 <= args.ingestion_rate <= 100
    ):
        parser.error("Fixture or sampling sizes exceed supported profiling bounds")
    if len(args.articles) > 1 and not args.seed:
        parser.error("Multiple fixture sizes require explicit --seed")
    engine = create_engine(url, connect_args={"prepare_threshold": None})
    output: dict[str, Any] = {"client_architecture": platform.machine(), "scales": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT current_database()").scalar_one() == url.database
            )
            output["server_version"] = connection.exec_driver_sql("SELECT version()").scalar_one()
            assert "PostgreSQL 17." in output["server_version"]
        for count in args.articles:
            if args.seed:
                print(f"Seeding {count} articles and retained job histories", flush=True)
                seed(engine, count)
            with engine.connect() as connection:
                scale = {
                    "articles": count,
                    "relations": relation_sizes(connection),
                    "histories": history_counts(connection),
                    "reports": [],
                }
            output["scales"].append(scale)
            for mode in ("custom", "generic"):
                for days in (1, 7, 30):
                    scale["reports"].append(
                        measure(
                            engine,
                            days,
                            mode,
                            args.repeats,
                            args.postgres_container,
                            args.ingestion_rate,
                        )
                    )
                    args.output.write_text(json.dumps(output, indent=2, default=str) + "\n")
                    print(
                        f"{count} articles / {days} days / {mode}: "
                        + " / ".join(
                            f"{name} {value['median_ms']:.1f} ms"
                            for name, value in scale["reports"][-1]["strategies"].items()
                        ),
                        flush=True,
                    )
    finally:
        engine.dispose()
    failures = [
        f"{scale['articles']} articles/{report['days']} days/{report['plan_mode']}: {name}"
        for scale in output["scales"]
        for report in scale["reports"]
        for name, passed in report["checks"].items()
        if not passed
    ]
    if failures:
        raise SystemExit("Benchmark checks failed (full evidence saved):\n" + "\n".join(failures))


if __name__ == "__main__":
    main()
