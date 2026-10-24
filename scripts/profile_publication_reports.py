"""Compare publication-report SQL on a disposable local PostgreSQL 17 database.

DEVFEED_TEST_DATABASE_URL must name a local database ending in _test. --seed
truncates article fixtures (including dependent rows). Apply migrations first.
No production rows or credentials are used. Full JSON plans are retained.
"""

import argparse
import json
import os
import platform
import statistics
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from devfeed_admin_api.automation import _publication_automation_query
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
BASELINE = """SELECT count(*), count(*) FILTER (
 WHERE EXISTS (SELECT 1 FROM article_reviews r WHERE r.article_id=a.id
   AND r.created_at<=a.published_to_feed_at AND r.action='publish' AND r.automation<>'{}'::jsonb)
 AND NOT EXISTS (SELECT 1 FROM article_reviews r WHERE r.article_id=a.id
   AND r.created_at<=a.published_to_feed_at AND r.automation='{}'::jsonb)),
 percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM
   a.published_to_feed_at-a.discovered_at)) FILTER (
   WHERE a.published_to_feed_at>=a.discovered_at)
 FROM articles a WHERE a.published_to_feed_at>=:start AND a.published_to_feed_at<=:now"""
ARTICLE_INSERT = """INSERT INTO articles
 (id,canonical_url,url_hash,title,slug,summary,content_type,feed_at,discovered_at,
  publication_status,review_status,published_to_feed_at)
 SELECT md5(:namespace||n)::uuid,'https://profile.invalid/'||:namespace||n,
 md5(:namespace||n), 'Publication fixture '||n, :namespace||n, repeat('fixture ',128),
 'article',:now, :now-(n%60)*interval '1 day'-(n%86400)*interval '1 second'
   -(CASE WHEN n%37=0 THEN -60 WHEN n%10 IN (1,3,6) THEN 91*86400
          ELSE 3600+n%100000 END)*interval '1 second',
 'published','approved',:now-(n%60)*interval '1 day'-(n%86400)*interval '1 second'
 FROM generate_series(CAST(:first AS integer),CAST(:last AS integer)) n"""


def seed_batches(connection, sql, rows, params):
    for first in range(1, rows + 1, 5000):
        connection.execute(text(sql), {**params, "first": first, "last": min(rows, first + 4999)})


def validate_target(value):
    url = make_url(value)
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"127.0.0.1", "localhost"}
        or not (url.database or "").endswith("_test")
        or url.query
        or any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"))
    ):
        raise ValueError(
            "Use a disposable local PostgreSQL database ending in _test without overrides"
        )
    return url


def seed(engine, rows):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE articles CASCADE"))
        seed_batches(connection, ARTICLE_INSERT, rows, {"now": NOW, "namespace": "publication-"})
        seed_batches(
            connection,
            """INSERT INTO article_reviews
            (id,article_id,action,revision,automation,created_at,note)
            SELECT md5('publication-review-'||n||'-'||f)::uuid,
            md5('publication-'||n)::uuid,
            CASE WHEN f=1 THEN 'publish' ELSE 'approve' END, f,
            CASE WHEN (f=2 AND n%10 IN (1,3,6)) OR (f=3 AND n%10=4)
              THEN '{}'::jsonb ELSE jsonb_build_object('profile',true) END,
            a.published_to_feed_at + CASE
              WHEN (f=1 AND n%10 IN (0,1,5,6)) OR (f=3 AND n%10=4)
                THEN interval '1 hour'
              WHEN f=2 THEN interval '-90 days' ELSE interval '-1 second' END,
            repeat('review evidence ',32)
            FROM generate_series(CAST(:first AS integer),CAST(:last AS integer)) n
            JOIN articles a ON a.id=md5('publication-'||n)::uuid
            CROSS JOIN LATERAL generate_series(1,CASE WHEN n%200=0 THEN 27 ELSE 3 END) f""",
            rows,
            {"rows": rows},
        )
        seed_batches(
            connection,
            """INSERT INTO article_analysis_jobs
            (id,article_id,status,attempts,available_at,created_at,finished_at,
             input_snapshot,catalog_snapshot,result)
            SELECT md5('publication-job-'||n)::uuid,
            md5('publication-'||(1+n%:rows))::uuid,'succeeded',1,
            :now-(n%180)*interval '1 day',:now-(n%180)*interval '1 day',:now,
            jsonb_build_object('text',repeat('historical payload ',32)),
            '{}','{}' FROM generate_series(CAST(:first AS integer),CAST(:last AS integer)) n""",
            rows * 165 // 100,
            {"now": NOW, "rows": rows},
        )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for table in ("articles", "article_reviews", "article_analysis_jobs"):
            connection.execute(text(f"VACUUM (ANALYZE) {table}"))


def backend_cpu(connection):
    # /proc/self belongs to this backend, including on a Docker service. Field 14
    # and 15 are user/system ticks; the two tiny sampling queries add fixed cost.
    value = connection.scalar(text("SELECT pg_read_file('/proc/self/stat')"))
    fields = value[value.rindex(")") + 2 :].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


class Ingestion:
    def __init__(self, engine):
        self.engine = engine
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.rows = 0
        self.errors = []
        self.samples_ms = []
        self.wal_bytes = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            with self.engine.connect() as connection:
                first_wal = connection.scalar(text("SELECT pg_current_wal_insert_lsn()"))
                connection.commit()
                self.ready.set()
                while not self.stop.is_set():
                    started = time.perf_counter()
                    identifier = uuid.uuid4()
                    with connection.begin():
                        connection.execute(
                            text("""INSERT INTO articles
                            (id,canonical_url,url_hash,title,slug,summary,content_type,
                             feed_at,discovered_at) VALUES
                            (:id,:url,:hash,'Concurrent fixture',:slug,'','article',:now,:now)"""),
                            {
                                "id": identifier,
                                "url": f"https://profile.invalid/{identifier}",
                                "hash": identifier.hex,
                                "slug": f"concurrent-{identifier}",
                                "now": NOW + timedelta(days=1),
                            },
                        )
                        connection.execute(
                            text("""INSERT INTO article_reviews
                            (id,article_id,action,revision,automation,created_at)
                            VALUES (:id,:article,'publish',1,
                                    jsonb_build_object('profile',true),:now)"""),
                            {"id": uuid.uuid4(), "article": identifier, "now": NOW},
                        )
                        connection.execute(
                            text("""INSERT INTO article_analysis_jobs
                            (id,article_id,status,attempts,available_at,created_at,
                             input_snapshot,catalog_snapshot,result)
                            VALUES (:id,:article,'queued',0,:now,:now,'{}','{}','{}')"""),
                            {"id": uuid.uuid4(), "article": identifier, "now": NOW},
                        )
                    self.rows += 1
                    self.samples_ms.append((time.perf_counter() - started) * 1000)
                    self.stop.wait(0.005)
                self.wal_bytes = int(
                    connection.scalar(
                        text("SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),:first)"),
                        {"first": first_wal},
                    )
                )
        except Exception as error:
            self.errors.append(str(error))
            self.ready.set()


def measure(engine, sql, params, repeats, concurrent):
    ingestion = Ingestion(engine) if concurrent else None
    if ingestion:
        ingestion.thread.start()
        if not ingestion.ready.wait(10):
            raise RuntimeError("Concurrent ingestion did not start")
    samples, cpu, app_cpu = [], [], []
    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            connection.execute(text("SET statement_timeout='60s'"))
            connection.execute(text("SET work_mem='4MB'"))
            connection.execute(text("SET max_parallel_workers_per_gather=0"))
            connection.execute(text(sql), params).one()  # One unmeasured warmup per strategy.
            for _ in range(repeats):
                before_cpu = backend_cpu(connection)
                before_app = time.thread_time()
                before = time.perf_counter()
                result = list(connection.execute(text(sql), params).one())
                samples.append((time.perf_counter() - before) * 1000)
                app_cpu.append((time.thread_time() - before_app) * 1000)
                cpu.append((backend_cpu(connection) - before_cpu) * 1000)
            plan = connection.scalar(
                text("EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON) " + sql), params
            )[0]
    finally:
        elapsed = time.perf_counter() - started
        if ingestion:
            ingestion.stop.set()
            ingestion.thread.join(10)
            if ingestion.thread.is_alive() or ingestion.errors:
                raise RuntimeError(f"Concurrent ingestion failed: {ingestion.errors}")
    output = {
        "result": result,
        "samples_ms": samples,
        "median_ms": statistics.median(samples),
        "backend_cpu_samples_ms": cpu,
        "application_thread_cpu_samples_ms": app_cpu,
        "plan": plan,
    }
    if ingestion:
        output["concurrent_ingestion"] = {
            "commits": ingestion.rows,
            "commits_per_second": ingestion.rows / elapsed,
            "median_commit_ms": statistics.median(ingestion.samples_ms),
            "max_commit_ms": max(ingestion.samples_ms),
            "wal_bytes": ingestion.wal_bytes,
            "wal_bytes_per_commit": ingestion.wal_bytes / ingestion.rows,
            "errors": ingestion.errors,
        }
    return output


def plan_nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from plan_nodes(child)


def check_work(report):
    baseline = report["strategies"]["baseline"]["plan"]["Plan"]
    candidate = report["strategies"]["candidate"]["plan"]["Plan"]

    def touches(node):
        return node.get("Shared Hit Blocks", 0) + node.get("Shared Read Blocks", 0)

    assert touches(candidate) < touches(baseline) * 0.75, "Broad report buffer work regressed"
    assert not any(
        node.get("Parent Relationship") == "SubPlan" for node in plan_nodes(candidate)
    ), "Candidate retained a per-publication correlated SubPlan"
    publications = report["strategies"]["candidate"]["result"][0]
    temp_bytes = 8192 * (
        candidate.get("Temp Read Blocks", 0) + candidate.get("Temp Written Blocks", 0)
    )
    assert temp_bytes <= publications * 512, (
        "Intermediate temporary work exceeded the cohort budget"
    )
    return {
        "candidate_buffer_fraction": touches(candidate) / touches(baseline),
        "no_correlated_subplan": True,
        "temporary_bytes": temp_bytes,
        "temporary_budget_bytes": publications * 512,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--inline-comparison", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        url = validate_target(os.environ["DEVFEED_TEST_DATABASE_URL"])
        if not 100 <= args.rows <= 1000000 or not 1 <= args.repeats <= 20:
            raise ValueError("Use 100..1000000 rows and 1..20 repetitions")
    except (ValueError, KeyError) as error:
        parser.error(str(error))
    engine = create_engine(url, pool_size=3)
    try:
        with engine.connect() as connection:
            version = connection.scalar(text("SELECT version()"))
            if connection.dialect.server_version_info[:1] != (17,):
                raise ValueError("Benchmark requires PostgreSQL 17")
            settings = dict(
                connection.execute(
                    text(
                        "SELECT name,setting FROM pg_settings WHERE name IN "
                        "('shared_buffers','work_mem','max_parallel_workers_per_gather',"
                        "'track_io_timing','jit','fsync','synchronous_commit')"
                    )
                ).all()
            )
        if args.seed:
            seed(engine, args.rows)
        output = {
            "rows": args.rows,
            "fixture_review_rows": args.rows * 3 + args.rows // 200 * 24,
            "fixture_analysis_job_rows": args.rows * 165 // 100,
            "client_architecture": platform.machine(),
            "postgresql": version,
            "settings": settings,
            "repeats": args.repeats,
            "reports": [],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        for concurrent in (False, True):
            for days in (1, 7, 30):
                params = {"start": NOW - timedelta(days=days), "now": NOW}
                statement = _publication_automation_query(params["start"], NOW)
                candidate = str(statement.compile(engine, compile_kwargs={"literal_binds": True}))
                strategies = {"baseline": BASELINE, "candidate": candidate}
                if args.inline_comparison:
                    strategies["candidate_inline"] = candidate.replace(
                        "AS MATERIALIZED", "AS NOT MATERIALIZED"
                    )
                report = {"days": days, "concurrent": concurrent, "strategies": {}}
                for name, sql in strategies.items():
                    report["strategies"][name] = measure(
                        engine, sql, params, args.repeats, concurrent
                    )
                    report["strategies"][name]["sql"] = sql
                results = [value["result"] for value in report["strategies"].values()]
                output["reports"].append(report)
                # Preserve failing plans and samples too; CI uploads partial output.
                args.output.write_text(json.dumps(output, indent=2) + "\n")
                assert all(result == results[0] for result in results), "Report results differ"
                report["result_equality"] = True
                if args.rows >= 100000 and days == 30:
                    report["work_checks"] = check_work(report)
                args.output.write_text(json.dumps(output, indent=2) + "\n")
                print(
                    json.dumps(
                        {
                            "days": days,
                            "concurrent": concurrent,
                            "strategies": {
                                name: round(value["median_ms"], 2)
                                for name, value in report["strategies"].items()
                            },
                        }
                    ),
                    flush=True,
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
