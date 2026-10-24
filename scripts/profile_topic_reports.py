"""Compare historical topic reports on an explicitly disposable local PostgreSQL 17+.

Run migrations first. --seed requires an empty database and never truncates data.
--baseline-root selects a clean checkout of the previous report implementation.
--require-postgres-major pins comparisons to a specific PostgreSQL major release.
All measured ingestion writes roll back. Only the projection trigger is temporarily
disabled, in short committed transactions, for the legacy concurrency comparison.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from devfeed_core import models
from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from scripts.profile_read_models import identifier, seed_rows

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
CONCURRENT_DAYS = 90
PRODUCTION = {
    "observed_at": "2026-10-24T09:29:00Z",
    "articles": 92581,
    "topic_proposals": 6011,
    "topic_decision_runs": 5000,
    "topic_calls": 14373,
    "sampled_state_bytes_mean": 20511,
    "sampled_state_bytes_p95": 35444,
    "sample_size": 100,
}


def validate_database(value):
    url = make_url(value)
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"127.0.0.1", "localhost"}
        or not (url.database or "").endswith("_test")
        or url.query
        or any(os.environ.get(name) for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"))
    ):
        raise ValueError(
            "Use an explicitly local PostgreSQL *_test database without routing overrides"
        )
    return url


def validate_server_version(value, *, required_major=None):
    major = int(value) // 10000
    if major < 17:
        raise ValueError("Topic report profiling requires PostgreSQL 17 or newer")
    if required_major is not None and major != required_major:
        raise ValueError(
            f"This comparison requires PostgreSQL {required_major}; connected server is {major}"
        )


def report_functions(root, label):
    functions = []
    for filename, function in (
        ("topic_decision_metrics.py", "decision_metrics"),
        ("inference_metrics.py", "inference_charts"),
    ):
        path = root / "packages/core/src" / filename
        name = f"topic_profile_{label}_{path.stem}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load report implementation {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        functions.append(getattr(module, function))
    return functions


class RecordingSession:
    def __init__(self, session):
        self.session = session
        self.statements = []

    def execute(self, statement, params=None):
        sql = str(statement)
        if "jsonb_array_elements" in sql or "FROM topic_decision_calls" in sql:
            self.statements.append((sql, params or {}))
        return self.session.execute(statement, params or {})

    def scalar(self, statement):
        return self.session.scalar(statement)


def plan_totals(plans):
    totals = dict.fromkeys(
        (
            "execution_ms",
            "shared_hit_blocks",
            "shared_read_blocks",
            "temp_read_blocks",
            "temp_written_blocks",
        ),
        0,
    )
    for plan in plans:
        totals["execution_ms"] += plan["Execution Time"]
        for output, key in (
            ("shared_hit_blocks", "Shared Hit Blocks"),
            ("shared_read_blocks", "Shared Read Blocks"),
            ("temp_read_blocks", "Temp Read Blocks"),
            ("temp_written_blocks", "Temp Written Blocks"),
        ):
            totals[output] += plan["Plan"].get(key, 0)
    return totals


def report_sample(engine, functions, days, *, explain=False):
    with Session(engine) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout='60s'"))
        session.execute(text("SET LOCAL work_mem='4MB'"))
        profile = RecordingSession(session)
        started, cpu = time.perf_counter(), time.process_time()
        output = [function(profile, NOW - timedelta(days=days), NOW) for function in functions]
        elapsed_ms = (time.perf_counter() - started) * 1000
        cpu_ms = (time.process_time() - cpu) * 1000
        plans = []
        if explain:
            for statement, params in profile.statements:
                plan = session.execute(
                    text("EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON) " + statement), params
                ).scalar_one()[0]
                plans.append(plan)
        return {
            "output": [value.model_dump(mode="json") for value in output],
            "elapsed_ms": round(elapsed_ms, 3),
            "application_cpu_ms": round(cpu_ms, 3),
            "plan_totals": plan_totals(plans),
            "plans": plans,
        }


def seed(engine, articles, runs, history_bytes):
    started = time.perf_counter()
    with engine.begin() as connection:
        if any(
            connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {table} LIMIT 1)"))
            for table in ("articles", "topic_proposals", "topic_decision_runs", "inference_calls")
        ):
            raise ValueError(
                "--seed requires empty fixture tables; create a new disposable database"
            )
        seed_rows(
            connection,
            models.Article,
            articles,
            {
                "canonical_url": "'https://topic-profile.invalid/articles/' || n",
                "url_hash": "md5('topic-profile-article-' || n)",
                "title": "'Topic report fixture ' || n",
                "summary": "'Historical article fixture for concurrent ingestion measurements'",
            },
        )
        proposal_id = identifier("topic-profile-proposal")
        # A pool of distinct incompressible strings avoids a misleading tiny TOAST
        # footprint from repeat('x', 20000), without millions of fixture hashes.
        connection.execute(
            text("""CREATE TEMP TABLE topic_profile_payloads ON COMMIT DROP AS
                SELECT n AS payload_index, array_to_string(ARRAY(SELECT md5((n*10000+s)::text)
                  FROM generate_series(1,:parts) s),'') AS payload
                FROM generate_series(0,63) n"""),
            {"parts": (history_bytes + 31) // 32},
        )
        connection.execute(
            text(f"""INSERT INTO topic_proposals
                (id,batch_id,slug,action,origin,source_name,proposed,evidence,status,
                 created_at,created_by,reviewed_at,reviewed_by,applied)
                SELECT {proposal_id},md5('topic-profile-batch')::uuid,
                  'topic-profile-'||n,'create','import','Topic profiler','{{}}','[]',
                  CASE WHEN n%10=0 THEN 'pending' WHEN n%10=1 THEN 'rejected' ELSE 'approved' END,
                  :now - (n%365)*interval '1 day','{{}}',
                  CASE WHEN n%10<>0 THEN :now - (n%120)*interval '1 day' END,
                  CASE WHEN n%10<>0 THEN '{{}}'::jsonb END,
                  CASE WHEN n%10 NOT IN (0,1) THEN '{{}}'::jsonb END
                FROM generate_series(1,:runs) n"""),
            {"now": NOW, "runs": runs},
        )
        # First 6,000 runs remain fixed as unrelated older history grows. Repeated
        # stage names span dates, so pushing the time filter before ordinality fails.
        connection.execute(
            text(f"""INSERT INTO topic_decision_runs
                (proposal_id,input_hash,status,reason,state,created_at,updated_at)
                SELECT {proposal_id},repeat('a',64),
                  CASE WHEN n%10=0 THEN 'deferred' ELSE 'decided' END,
                  CASE WHEN n%10=0 THEN 'profile_budget' END,
                  jsonb_build_object('evidence',payload,'calls',
                    (SELECT jsonb_agg(jsonb_build_object(
                      'stage',(ARRAY['discovery','draft','verification','draft','verification'])[s],
                      'started_at',to_char((:now -
                        (CASE WHEN n<=6000 THEN n%90 ELSE 180+n%185 END)*interval '1 day'
                        - (6-s)*interval '6 hours' - interval '1 hour') AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                      'status',CASE WHEN n%19=0 AND s=1 THEN 'failed' ELSE 'returned' END,
                      'tokens',jsonb_build_object('inputTokens',1000+n%1000,
                        'outputTokens',100+s,'totalTokens',1100+n%1000+s),
                      'charged_tokens',1200+n%1000+s,'escalated',s>3)
                      ORDER BY s) FROM generate_series(1,1+n%5) s)),
                  :now - (n%365)*interval '1 day',:now - (n%120)*interval '1 day'
                FROM generate_series(1,:runs) n
                JOIN topic_profile_payloads p ON p.payload_index=n%64"""),
            {"now": NOW, "runs": runs},
        )
        connection.execute(
            text("""INSERT INTO inference_calls
              (id,started_at,finished_at,operation,model,request_hash,status,tokens,web_searches,duration_ms)
              SELECT md5('topic-profile-inference-'||n)::uuid,
                :now-(n%365)*interval '1 day',:now,'topic_discovery','fixture',repeat('b',64),
                'returned',jsonb_build_object('inputTokens',1000,'outputTokens',100),0,100
              FROM generate_series(1,:rows) n"""),
            {"now": NOW, "rows": articles * 5},
        )
    with engine.begin() as connection:
        for table in (
            "articles",
            "topic_proposals",
            "topic_decision_runs",
            "topic_decision_calls",
            "inference_calls",
            "inference_daily_usage",
        ):
            connection.execute(text(f"ANALYZE {table}"))
    return {"elapsed_seconds": round(time.perf_counter() - started, 3)}


def set_projection(engine, enabled):
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout='5s'"))
        connection.execute(
            text(
                "ALTER TABLE topic_decision_runs "
                + ("ENABLE" if enabled else "DISABLE")
                + " TRIGGER topic_decision_calls_sync"
            )
        )


def maintenance_profile(engine):
    """Measure a full equivalent backfill, restoring facts with transaction rollback."""
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("TRUNCATE topic_decision_calls"))
            plan = connection.execute(
                text("""EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON)
                INSERT INTO topic_decision_calls
                SELECT r.proposal_id,c.* FROM topic_decision_runs r
                CROSS JOIN LATERAL topic_decision_call_values(r.state->'calls') c""")
            )
            return plan.scalar_one()[0]
        finally:
            transaction.rollback()


def _correction_plan(engine, legacy):
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            proposal = connection.scalar(
                text("""SELECT proposal_id FROM topic_decision_calls
                GROUP BY proposal_id ORDER BY count(*) DESC,proposal_id LIMIT 1""")
            )
            if legacy:
                connection.execute(
                    text("""ALTER TABLE topic_decision_runs
                    DISABLE TRIGGER topic_decision_calls_sync""")
                )
            before = connection.scalar(text("SELECT pg_current_wal_insert_lsn()"))
            plan = connection.execute(
                text("""EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON)
                UPDATE topic_decision_runs SET state=jsonb_set(state,
                  '{calls,0,charged_tokens}','50000'::jsonb)
                WHERE proposal_id=:proposal"""),
                {"proposal": proposal},
            ).scalar_one()[0]
            plan["statement_wal_bytes"] = int(
                connection.scalar(
                    text("SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),:before)"),
                    {"before": before},
                )
            )
            plan["statement_wal_scope"] = (
                "Server-wide LSN delta including AFTER triggers and any background WAL; "
                "excludes later transaction rollback"
            )
            return plan
        finally:
            transaction.rollback()


def correction_plan(engine, legacy):
    # Prime the same pages first: otherwise the first strategy pays checkpoint-
    # dependent full-page images and makes WAL amplification appear misleading.
    _correction_plan(engine, legacy)
    return _correction_plan(engine, legacy)


def writer(engine, iterations, barrier=None, active=None, interval=0, runs=50):
    samples = []
    if active is not None:
        active.set()
    if barrier is not None:
        barrier.wait()
    cpu, started = time.thread_time(), time.perf_counter()
    for index in range(iterations):
        number = 1 + index % min(runs, 50)
        proposal = uuid.UUID(
            hashlib.sha256(
                f"read-model-profile:topic-profile-proposal:{number}".encode()
            ).hexdigest()[:32]
        )
        with engine.connect() as connection:
            transaction = connection.begin()
            sample_started = time.perf_counter()
            try:
                value = uuid.uuid4()
                connection.execute(
                    insert(models.Article).values(
                        id=value,
                        canonical_url=f"https://topic-profile.invalid/ingest/{value}",
                        url_hash=hashlib.sha256(value.bytes).hexdigest(),
                        title="Concurrent ingestion",
                    )
                )
                connection.execute(
                    text("""UPDATE topic_decision_runs SET state=jsonb_set(state,
                      '{calls,0,charged_tokens}',to_jsonb(CAST(:tokens AS bigint))),updated_at=:now
                      WHERE proposal_id=:proposal"""),
                    {
                        "tokens": 50000 + index,
                        "now": NOW,
                        "proposal": proposal,
                    },
                )
                samples.append((time.perf_counter() - sample_started) * 1000)
            finally:
                transaction.rollback()
        if interval:
            time.sleep(interval)
    if active is not None:
        active.clear()
    return {
        "iterations": iterations,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "application_thread_cpu_seconds": round(time.thread_time() - cpu, 3),
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(sorted(samples)[min(len(samples) - 1, int(len(samples) * 0.95))], 3),
        "committed": False,
    }


def concurrent(engine, functions, workers, repeats, writes, interval, runs):
    barrier = threading.Barrier(workers + 1)
    active = threading.Event()

    def reporter():
        barrier.wait()
        samples = []
        for _ in range(repeats):
            during_start = active.is_set()
            sample = report_sample(engine, functions, CONCURRENT_DAYS, explain=True)
            sample["writer_phase_overlap"] = during_start and active.is_set()
            samples.append(sample)
        return samples

    started, cpu = time.perf_counter(), time.process_time()
    with ThreadPoolExecutor(max_workers=workers + 1) as executor:
        reports = [executor.submit(reporter) for _ in range(workers)]
        ingestion = executor.submit(writer, engine, writes, barrier, active, interval, runs)
        samples = [sample for future in reports for sample in future.result()]
        ingestion_result = ingestion.result()
    return {
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "application_process_cpu_seconds": round(time.process_time() - cpu, 3),
        "report_workers": workers,
        "days": CONCURRENT_DAYS,
        "report_pairs": len(samples),
        "writer_interval_seconds": round(interval, 6),
        "writer_phase_overlap_pairs": sum(s["writer_phase_overlap"] for s in samples),
        "median_report_pair_ms": round(statistics.median(s["elapsed_ms"] for s in samples), 3),
        "report_plan_totals": plan_totals([plan for sample in samples for plan in sample["plans"]]),
        "ingestion": ingestion_result,
        "plans": [sample["plans"] for sample in samples],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--articles", type=int, default=100000)
    parser.add_argument("--runs", type=int)
    parser.add_argument("--history-bytes", type=int, default=20000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--writes", type=int, default=200)
    parser.add_argument("--require-improvement", action="store_true")
    parser.add_argument("--require-postgres-major", type=int)
    args = parser.parse_args()
    try:
        url = validate_database(os.environ["DEVFEED_TEST_DATABASE_URL"])
    except (KeyError, ValueError) as error:
        parser.error(str(error))
    if args.require_postgres_major is not None and args.require_postgres_major < 17:
        parser.error("--require-postgres-major must be 17 or newer")
    runs = args.runs if args.runs is not None else max(6000, args.articles * 6 // 100)
    if not (
        10 <= args.articles <= 1000000
        and 10 <= runs <= 100000
        and 0 <= args.history_bytes <= 40000
        and 1 <= args.repeats <= 10
        and 1 <= args.workers <= 8
        and 1 <= args.writes <= 1000
    ):
        parser.error("Fixture and concurrency sizes exceed supported profiling bounds")
    functions = {
        "legacy": report_functions(args.baseline_root.resolve(), "legacy"),
        "facts": report_functions(ROOT, "facts"),
    }
    engine = create_engine(url, pool_size=12, max_overflow=0)
    output = {
        "production_reference": PRODUCTION,
        "client_architecture": platform.machine(),
        "python_version": platform.python_version(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "report_source_sha256": {
            name: {
                Path(sys.modules[function.__module__].__file__).name: hashlib.sha256(
                    Path(sys.modules[function.__module__].__file__).read_bytes()
                ).hexdigest()
                for function in implementations
            }
            for name, implementations in functions.items()
        },
    }
    try:
        with engine.connect() as connection:
            output["server"] = dict(
                connection.execute(
                    text("""SELECT version() AS version,
                current_setting('work_mem') AS default_work_mem,
                current_setting('server_version_num') AS version_num""")
                )
                .mappings()
                .one()
            )
            try:
                validate_server_version(
                    output["server"]["version_num"], required_major=args.require_postgres_major
                )
            except ValueError as error:
                parser.error(str(error))
        if args.seed:
            output["seed"] = seed(engine, args.articles, runs, args.history_bytes)
        with engine.connect() as connection:
            output["fixture"] = dict(
                connection.execute(
                    text("""SELECT
                (SELECT count(*) FROM articles) AS articles,
                (SELECT count(*) FROM topic_decision_runs) AS runs,
                (SELECT count(*) FROM topic_decision_calls) AS calls,
                (SELECT count(*) FROM inference_calls) AS inference_calls""")
                )
                .mappings()
                .one()
            )
        output["reports"] = []
        for days in (1, 7, 30, 90):
            samples = {name: [] for name in functions}
            expected = None
            for repeat in range(args.repeats):
                for name in ("legacy", "facts") if repeat % 2 == 0 else ("facts", "legacy"):
                    sample = report_sample(engine, functions[name], days, explain=repeat == 0)
                    actual = sample.pop("output")
                    if expected is not None and actual != expected:
                        raise AssertionError(f"{days}-day report output differs for {name}")
                    expected = actual
                    samples[name].append(sample)
            output["reports"].append(
                {
                    "days": days,
                    "exact_output_parity": True,
                    "output_sha256": hashlib.sha256(
                        json.dumps(expected, sort_keys=True).encode()
                    ).hexdigest(),
                    "strategies": samples,
                }
            )
        output["backfill_equivalent_plan"] = maintenance_profile(engine)
        output["correction_plans"] = {
            name: correction_plan(engine, name == "legacy") for name in functions
        }
        output["concurrent"] = {}
        # Pace identical fixed ingestion workloads long enough to cover all report
        # repetitions. Pacing waits are excluded from each transaction's latency.
        comparison_window = next(row for row in output["reports"] if row["days"] == CONCURRENT_DAYS)
        legacy_ms = statistics.median(
            sample["elapsed_ms"] for sample in comparison_window["strategies"]["legacy"]
        )
        interval = min(
            0.25, max(0.002, legacy_ms / 1000 * args.repeats * args.workers * 4 / args.writes)
        )
        for name in ("legacy", "facts"):
            set_projection(engine, name == "facts")
            try:
                output["concurrent"][name] = concurrent(
                    engine,
                    functions[name],
                    args.workers,
                    args.repeats,
                    args.writes,
                    interval,
                    output["fixture"]["runs"],
                )
            finally:
                set_projection(engine, True)
        legacy, facts = (output["concurrent"][name] for name in ("legacy", "facts"))
        output["improvement"] = {
            "report_runtime_lower": facts["median_report_pair_ms"]
            < legacy["median_report_pair_ms"],
            "topic_sql_runtime_lower": facts["report_plan_totals"]["execution_ms"]
            < legacy["report_plan_totals"]["execution_ms"],
            "topic_sql_zero_temp_writes": facts["report_plan_totals"]["temp_written_blocks"] == 0,
            "all_reports_overlap_writer_phase": all(
                row["writer_phase_overlap_pairs"] == row["report_pairs"]
                for row in output["concurrent"].values()
            ),
        }
        output["complete"] = True
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "fixture": output["fixture"],
                    "concurrent": {
                        name: {key: value for key, value in result.items() if key != "plans"}
                        for name, result in output["concurrent"].items()
                    },
                },
                indent=2,
            )
        )
        if args.require_improvement and not all(output["improvement"].values()):
            raise AssertionError(f"Profile acceptance failed: {output['improvement']}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
