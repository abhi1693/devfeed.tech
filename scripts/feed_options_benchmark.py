"""Compare real feed-options implementations on an isolated PostgreSQL 17 database.

The only accepted target is loopback/feed_options_benchmark_test. This CLI destroys
its synthetic fixture between scales; never point it at an application database.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import statistics
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    IngestionJob,
    Source,
    Tag,
    Topic,
)
from devfeed_core.schemas import FeedOptionsOut
from sqlalchemy import create_engine, event, insert, literal_column, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
DATABASE = "feed_options_benchmark_test"


def load_feed(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.feed_options


def seed_insert(connection, model, expressions, count):
    query = select(*(literal_column(value).label(key) for key, value in expressions.items()))
    query = query.select_from(text("generate_series(1, :count) AS fixture(n)"))
    connection.execute(
        insert(model.__table__).from_select(list(expressions), query), {"count": count}
    )


def fixture(connection, rows, sources):
    # All identifiers and distributions are deterministic. Mix short and long
    # histories per source instead of giving every source the same row count.
    seed_insert(
        connection,
        Source,
        dict(
            id="md5('source-' || n)::uuid",
            name="'Publisher ' || lpad((n / 2)::text, 6, '0')",
            slug="'publisher-' || n",
            feed_url="'https://publisher-' || n || '.example.test/feed'",
            source_type="'publisher'",
            description="repeat('Public publisher description. ', 12)",
            language="'en'",
            enabled="n % 211 <> 0",
            approval_status="CASE WHEN n % 17 = 0 THEN 'pending' ELSE 'approved' END",
            # Realistic private editorial payloads exercise wide hydration.
            relevance_assessment="jsonb_build_object('evidence', repeat(md5(n::text), 64))",
            processing_request="jsonb_build_object('request', repeat(md5((n+1)::text), 32))",
            last_error="repeat('Historical ingestion diagnostic. ', 20)",
        ),
        sources,
    )
    seed_insert(
        connection,
        Topic,
        dict(
            id="md5('topic-' || n)::uuid",
            name="'Technology ' || n",
            slug="'technology-' || n",
            kind="'technology'",
            status="'active'",
        ),
        40,
    )
    seed_insert(
        connection,
        Tag,
        dict(id="md5('tag-' || n)::uuid", name="'Tag ' || n", slug="'tag-' || n"),
        100,
    )
    append_articles(connection, 0, rows, sources)
    seed_insert(
        connection,
        ArticleTag,
        dict(
            article_id="md5('article-' || ((n - 1) / 9 + 1))::uuid",
            tag_id="md5('tag-' || (n % 100 + 1))::uuid",
        ),
        rows * 9,
    )
    seed_insert(
        connection,
        ArticleTopic,
        dict(
            article_id="md5('article-' || ((n - 1) / 4 + 1))::uuid",
            topic_id="md5('topic-' || (n % 40 + 1))::uuid",
            role="CASE WHEN n % 11 = 0 THEN 'incidental' ELSE 'primary' END",
            relevance="0.9",
            evidence="'Synthetic benchmark topic evidence'",
        ),
        rows * 4,
    )
    seed_insert(
        connection,
        IngestionJob,
        dict(
            id="md5('ingestion-' || n)::uuid",
            source_id=f"md5('source-' || (n % {sources} + 1))::uuid",
            status="'succeeded'",
            entries_seen="40",
            articles_created="10",
            finished_at="now() - (n % 365) * interval '1 day'",
        ),
        round(rows * 1.37),
    )
    seed_insert(
        connection,
        ArticleAnalysisJob,
        dict(
            id="md5('analysis-history-' || n)::uuid",
            article_id=f"md5('article-' || (n % {rows} + 1))::uuid",
            status="'succeeded'",
            input_snapshot="jsonb_build_object('title', 'Earlier analysis input')",
            result="jsonb_build_object('decision', 'approved')",
            finished_at="now() - interval '30 days'",
        ),
        round(rows * 0.7),
    )
    seed_insert(
        connection,
        ArticleEnrichmentJob,
        dict(
            id="md5('enrichment-history-' || n)::uuid",
            article_id=f"md5('article-' || (n % {rows} + 1))::uuid",
            status="'succeeded'",
            finished_at="now() - interval '30 days'",
        ),
        round(rows * 1.13),
    )


def append_articles(connection, offset, count, sources):
    # Parameterized offset is a trusted integer from this CLI, never user SQL.
    n = f"(n + {offset})"
    seed_insert(
        connection,
        Article,
        dict(
            id=f"md5('article-' || {n})::uuid",
            slug=f"'article-' || {n}",
            canonical_url=f"'https://article.example.test/' || {n}",
            url_hash=f"md5('article-' || {n})",
            title=f"'Database engineering ' || {n} || CASE WHEN {n} % 251 = 0 "
            "THEN ' raretoken' ELSE '' END",
            summary="repeat('Database API engineering and infrastructure. ', 20)",
            language=f"CASE WHEN {n} % 101 = 0 THEN 'fr' WHEN {n} % 37 = 0 THEN 'de' ELSE 'en' END",
            content_type=f"CASE WHEN {n} % 100 < 39 THEN 'article' "
            f"WHEN {n} % 100 < 58 THEN 'tutorial' WHEN {n} % 100 < 75 THEN 'release' "
            f"WHEN {n} % 100 < 89 THEN 'news' WHEN {n} % 100 < 98 THEN 'opinion' "
            "ELSE 'comparison' END",
            publication_status=f"CASE WHEN ({n} * 37) % 100 < 79 "
            "THEN 'published' ELSE 'unpublished' END",
            review_status=f"CASE WHEN ({n} * 37) % 100 < 85 THEN 'approved' ELSE 'pending' END",
            feed_at=f"now() - ({n} % 86400) * interval '1 second'",
        ),
        count,
    )
    # Two hot publishers receive about 4% each; the remaining history is spread
    # across the long tail independently of publication/language/type predicates.
    source = (
        f"CASE WHEN {n} % 25 = 1 THEN 1 WHEN {n} % 25 = 2 THEN 2 "
        f"ELSE (({n} * 7919) / 100) % {sources} + 1 END"
    )
    for origin in (0, 1):
        expressions = dict(
            id=f"md5('origin-{origin}-' || {n})::uuid",
            article_id=f"md5('article-' || {n})::uuid",
            source_id=f"md5('source-' || ({source}))::uuid",
            entry_key=f"'origin-{origin}-' || {n}",
            original_url=f"'https://article.example.test/' || {n}",
        )
        # Additional origin every tenth row includes same-source duplicate origins.
        if origin:
            expressions = {
                k: v.replace(n, f"((n * 10) + {offset})") for k, v in expressions.items()
            }
        seed_insert(connection, ArticleOrigin, expressions, count if not origin else count // 10)
    seed_insert(
        connection,
        ArticleAnalysisJob,
        dict(
            id=f"md5('analysis-' || {n})::uuid",
            article_id=f"md5('article-' || {n})::uuid",
            status="'succeeded'",
            input_snapshot="jsonb_build_object('title', 'Historical analysis input')",
            result="jsonb_build_object('decision', 'approved')",
            finished_at="now() - interval '1 hour'",
        ),
        count,
    )


def measure(connection, function, filters):
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(connection, "before_cursor_execute", capture)
    try:
        started, cpu = time.perf_counter(), time.thread_time()
        with Session(bind=connection) as session:
            result = FeedOptionsOut.model_validate(function(session, **filters)).model_dump(
                mode="json"
            )
            # Include real response serialization and ORM hydration in application CPU.
            response = json.dumps(result, sort_keys=True)
        wall_ms = (time.perf_counter() - started) * 1000
        cpu_ms = (time.thread_time() - cpu) * 1000
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    return response, dict(wall_ms=wall_ms, application_cpu_ms=cpu_ms), statements


def actual_plans(connection, statements):
    plans = []
    for statement, parameters in statements:
        plan = connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON) " + statement, parameters
        ).scalar_one()[0]
        plans.append(dict(sql=statement, plan=plan))
    return plans


def plan_metrics(plans):
    # Source hydration is the final route query for both broad and filtered paths.
    source = plans[-1]["plan"]
    nodes = []

    def walk(node):
        nodes.append(node)
        for child in node.get("Plans", []):
            walk(child)

    walk(source["Plan"])
    root = source["Plan"]
    return dict(
        source_sql_ms=source["Execution Time"],
        shared_hit_blocks=root.get("Shared Hit Blocks", 0),
        shared_read_blocks=root.get("Shared Read Blocks", 0),
        temp_written_blocks=root.get("Temp Written Blocks", 0),
        temp_read_blocks=root.get("Temp Read Blocks", 0),
        article_probe_loops=sum(
            node["Actual Loops"] for node in nodes if node.get("Relation Name") == "articles"
        ),
        origin_probe_loops=sum(
            node["Actual Loops"] for node in nodes if node.get("Relation Name") == "article_origins"
        ),
        sorts=[
            dict(width=node["Plan Width"], rows=node["Actual Rows"], loops=node["Actual Loops"])
            for node in nodes
            if node["Node Type"] in {"Sort", "Incremental Sort"}
        ],
    )


def write_report(output, report):
    (output / "results.json").write_text(json.dumps(report, indent=2))
    lines = [
        "# Feed-options source-facet benchmark",
        "",
        f"Host: {report['architecture']}; PostgreSQL: {report['postgres_version']}.",
        f"Baseline: `{report['baseline_sha']}`; candidate: `{report['candidate_sha']}`.",
        "",
        "Warm-cache medians for actual route calls, validation and JSON serialization. "
        "Application CPU is request-thread CPU. Both versions share one repeatable-read "
        "snapshot per pair; order alternates. EXPLAIN runs after both timed calls.",
        "",
        "| Articles | Case | Ingestion | Baseline ms | Candidate ms | CPU base/head ms | "
        "Source SQL base/head ms | Origin loops base/head | Hits base/head | "
        "Temp writes base/head |",
        "|---:|---|---|---:|---:|---|---|---|---|---|",
    ]
    for case in report["cases"]:
        base, head = case["baseline"], case["candidate"]
        bp, hp = case["plan_metrics"]["baseline"], case["plan_metrics"]["candidate"]
        lines.append(
            f"| {case['rows']} | {case['case']} | {case['concurrent_ingestion']} | "
            f"{base['wall_ms']:.2f} | {head['wall_ms']:.2f} | "
            f"{base['application_cpu_ms']:.2f}/{head['application_cpu_ms']:.2f} | "
            f"{bp['source_sql_ms']:.2f}/{hp['source_sql_ms']:.2f} | "
            f"{bp['origin_probe_loops']}/{hp['origin_probe_loops']} | "
            f"{bp['shared_hit_blocks']}/{hp['shared_hit_blocks']} | "
            f"{bp['temp_written_blocks']}/{hp['temp_written_blocks']} |"
        )
    lines += [
        "",
        "Every reported pair returned identical public JSON. Full SQL/plans include "
        "actual loops, sort widths, buffers, temporary reads/writes and WAL. "
        "Dataset metadata and shared mixed-workload ingestion commits are in results.json.",
        "",
        "This is an isolated synthetic database benchmark, not production latency or "
        "an HTTP/cache/load-capacity test. Historical bulk loading bypasses USER triggers "
        "with FKs/indexes enabled; measured ingestion executes all migrated triggers. "
        "Ingestion metrics describe contention shared by both revisions, not a separate "
        "before/after write-amplification experiment. No index/schema change is proposed.",
    ]
    (output / "summary.md").write_text("\n".join(lines) + "\n")


def ingest(engine, sources, start, stop, result):
    offset = start
    while not stop.is_set():
        wall, cpu = time.perf_counter(), time.thread_time()
        try:
            with engine.begin() as connection:
                append_articles(connection, offset, 20, sources)
            result.append(
                dict(
                    articles=20,
                    wall_ms=(time.perf_counter() - wall) * 1000,
                    application_cpu_ms=(time.thread_time() - cpu) * 1000,
                )
            )
            offset += 20
        except Exception as error:
            result.append(dict(error=repr(error)))
            stop.set()
            return
        stop.wait(0.02)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--rows", type=int, nargs="+", default=[2000, 100000, 1000000])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("reports/feed-options-benchmark"))
    parser.add_argument("--require-arm64", action="store_true")
    args = parser.parse_args()
    if not all(1000 <= rows <= 2000000 for rows in args.rows) or not 1 <= args.repeats <= 20:
        parser.error("Use 1000..2000000 articles and 1..20 repeats")
    url = os.environ["DEVFEED_TEST_DATABASE_URL"]
    parsed = make_url(url)
    if parsed.host != "127.0.0.1" or parsed.database != DATABASE:
        parser.error(f"Target must be loopback/{DATABASE}; this benchmark destroys its fixture")
    if args.require_arm64 and platform.machine() not in {"aarch64", "arm64"}:
        parser.error("A native ARM64 host is required")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "reports"):
        parser.error("Output must stay in this checkout's reports directory")
    output.mkdir(parents=True, exist_ok=True)
    # Explicitly prepared calls allow bounded force_generic_plan execution cases.
    engine = create_engine(url, pool_size=4, connect_args={"prepare_threshold": 0})
    baseline_sha = subprocess.check_output(
        ["git", "rev-parse", f"{args.baseline_ref}^{{commit}}"], cwd=ROOT, text=True
    ).strip()
    baseline = subprocess.check_output(
        ["git", "show", f"{baseline_sha}:apps/api/src/feed.py"], cwd=ROOT, text=True
    )
    report = dict(
        started_at=datetime.now(UTC).isoformat(),
        architecture=platform.machine(),
        logical_cpus=os.cpu_count(),
        baseline_sha=baseline_sha,
        candidate_sha=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        candidate_feed_sha256=hashlib.sha256(
            (ROOT / "apps/api/src/feed.py").read_bytes()
        ).hexdigest(),
        candidate_dirty=bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        prepare_threshold=0,
        plan_evidence="EXPLAIN ANALYZE replay with supplied binds after paired timings; "
        "not a capture of the exact server-prepared execution plan",
        cases=[],
    )
    try:
        with engine.connect() as connection:
            if connection.scalar(
                text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
            ):
                parser.error("Start with an empty disposable database")
            version = connection.scalar(text("SHOW server_version"))
            if not version.startswith("17."):
                parser.error("PostgreSQL 17 is required")
            report["postgres_version"] = version
            report["postgres_build"] = connection.scalar(text("SELECT version()"))
            if args.require_arm64 and "aarch64" not in report["postgres_build"]:
                parser.error("A native ARM64 PostgreSQL build is required")
            report["settings"] = dict(
                connection.execute(
                    text(
                        "SELECT name, setting FROM pg_settings WHERE name IN "
                        "('shared_buffers','work_mem','effective_cache_size','random_page_cost',"
                        "'max_parallel_workers_per_gather','track_io_timing')"
                    )
                ).all()
            )
        with tempfile.TemporaryDirectory(prefix="devfeed-options-benchmark-") as temporary:
            temp = Path(temporary)
            # Settings otherwise discover the caller's .env. Only this disposable
            # database and deliberately unreachable Redis are available here.
            os.chdir(temp)
            for key in list(os.environ):
                if key.startswith("DEVFEED_"):
                    del os.environ[key]
            os.environ.update(
                DEVFEED_DATABASE_URL=url,
                DEVFEED_REDIS_URL="redis://127.0.0.1:1/15",
                DEVFEED_CACHE_ENABLED="false",
                DEVFEED_AI_ENABLED="false",
                DEVFEED_FULL_AUTOMATION="false",
            )
            from devfeed_core.config import get_settings

            get_settings.cache_clear()
            baseline_path = temp / "baseline_feed.py"
            baseline_path.write_text(baseline)
            functions = dict(
                baseline=load_feed(baseline_path, "benchmark_baseline_feed"),
                candidate=load_feed(ROOT / "apps/api/src/feed.py", "benchmark_candidate_feed"),
            )
            env = {k: v for k, v in os.environ.items() if not k.startswith("DEVFEED_")}
            env.update(DEVFEED_DATABASE_URL=url, DEVFEED_REDIS_URL="redis://127.0.0.1:1/15")
            subprocess.run(
                [
                    str(ROOT / ".venv/bin/python"),
                    "-m",
                    "alembic",
                    "-c",
                    str(ROOT / "alembic.ini"),
                    "upgrade",
                    "head",
                ],
                env=env,
                cwd=temp,
                check=True,
            )
            for rows in args.rows:
                sources = max(1406, round(rows * 1406 / 100000))
                print(f"Seeding {rows} articles / {sources} sources", flush=True)
                with engine.begin() as connection:
                    tables = connection.scalars(
                        text(
                            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                            "AND tablename <> 'alembic_version'"
                        )
                    ).all()
                    connection.exec_driver_sql(
                        "TRUNCATE " + ", ".join('"' + name + '"' for name in tables) + " CASCADE"
                    )
                    # Bulk load historical rows without replaying millions of cache
                    # invalidations/read-model events. FKs and indexes remain active;
                    # all user triggers run for measured concurrent ingestion below.
                    for name in tables:
                        connection.exec_driver_sql(f'ALTER TABLE "{name}" DISABLE TRIGGER USER')
                    fixture(connection, rows, sources)
                    for name in tables:
                        connection.exec_driver_sql(f'ALTER TABLE "{name}" ENABLE TRIGGER USER')
                with engine.begin() as connection:
                    connection.exec_driver_sql("ANALYZE")
                    metadata = dict(rows=rows, sources=sources, table_counts={})
                    for table in (
                        "articles",
                        "sources",
                        "article_origins",
                        "article_tags",
                        "article_topics",
                        "article_analysis_jobs",
                        "article_enrichment_jobs",
                        "ingestion_jobs",
                    ):
                        metadata["table_counts"][table] = connection.scalar(
                            text(f'SELECT count(*) FROM "{table}"')
                        )
                    metadata["article_distribution"] = [
                        dict(row)
                        for row in connection.execute(
                            text(
                                "SELECT publication_status, review_status, language, content_type, "
                                "count(*) AS rows FROM articles GROUP BY 1,2,3,4 ORDER BY 1,2,3,4"
                            )
                        ).mappings()
                    ]
                    metadata["eligible_english_sources"] = connection.scalar(
                        text(
                            "SELECT count(DISTINCT s.id) FROM sources s JOIN article_origins o "
                            "ON o.source_id=s.id JOIN articles a ON a.id=o.article_id "
                            "WHERE s.enabled AND s.approval_status='approved' "
                            "AND a.publication_status='published' AND a.review_status='approved' "
                            "AND a.language='en'"
                        )
                    )
                    if rows >= 100000 and metadata["eligible_english_sources"] <= 500:
                        raise AssertionError("Fixture must exercise the 500-source limit")
                    report.setdefault("datasets", []).append(metadata)
                cases = dict(
                    broad_english=dict(languages=["en"]),
                    all_languages={},
                    rare_language=dict(languages=["fr"]),
                    absent_language=dict(languages=["ja"]),
                    rare_kind=dict(languages=["en"], content_type="opinion"),
                    filtered_combination=dict(
                        languages=["en"], topic="technology-2", tag=["tag-2"]
                    ),
                    selective_search=dict(q="raretoken", languages=["en"]),
                )
                if rows >= 100000:
                    cases.update(
                        generic_broad_english=dict(languages=["en"]),
                        generic_rare_language=dict(languages=["fr"]),
                    )
                for concurrent in (False, True):
                    stop, writes = threading.Event(), []
                    writer = None
                    if concurrent:
                        writer = threading.Thread(
                            target=ingest, args=(engine, sources, rows, stop, writes), daemon=True
                        )
                        writer.start()
                    try:
                        for name, selected in cases.items():
                            filters = (
                                dict(
                                    q=None,
                                    topic=None,
                                    tag=None,
                                    content_type=None,
                                    source_id=None,
                                    languages=None,
                                )
                                | selected
                            )
                            case = dict(
                                rows=rows,
                                sources=sources,
                                case=name,
                                concurrent_ingestion=concurrent,
                                samples=[],
                                plan_cache_mode=(
                                    "force_generic_plan" if name.startswith("generic_") else "auto"
                                ),
                            )
                            # Both revisions share one snapshot per pair, including while
                            # ingestion commits. Alternate order to reduce warm-cache bias.
                            for repeat in range(args.repeats + 1):
                                with (
                                    engine.connect().execution_options(
                                        isolation_level="REPEATABLE READ"
                                    ) as connection,
                                    connection.begin(),
                                ):
                                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                                    connection.exec_driver_sql(
                                        "SET LOCAL statement_timeout = '60s'"
                                    )
                                    connection.exec_driver_sql(
                                        "SET LOCAL plan_cache_mode = " + case["plan_cache_mode"]
                                    )
                                    outputs, sample, captured = {}, {}, {}
                                    order = (
                                        ("baseline", "candidate")
                                        if repeat % 2 == 0
                                        else ("candidate", "baseline")
                                    )
                                    for side in order:
                                        outputs[side], sample[side], statements = measure(
                                            connection, functions[side], filters
                                        )
                                        captured[side] = statements
                                    if repeat == args.repeats:
                                        for side in order:
                                            plans = actual_plans(connection, captured[side])
                                            filename = (
                                                f"{rows}-{name}-{int(concurrent)}-{side}.json"
                                            )
                                            (output / filename).write_text(
                                                json.dumps(plans, indent=2)
                                            )
                                            sample[side]["plans"] = filename
                                            case.setdefault("plan_metrics", {})[side] = (
                                                plan_metrics(plans)
                                            )
                                    if outputs["baseline"] != outputs["candidate"]:
                                        raise AssertionError(
                                            f"Result mismatch: {rows}/{name}/{concurrent}"
                                        )
                                    if repeat:
                                        case["samples"].append(sample)
                            case["equivalent"] = True
                            for side in functions:
                                case[side] = {
                                    key: statistics.median(
                                        sample[side][key] for sample in case["samples"]
                                    )
                                    for key in ("wall_ms", "application_cpu_ms")
                                }
                            report["cases"].append(case)
                            write_report(output, report)
                            print(
                                json.dumps(
                                    {key: value for key, value in case.items() if key != "samples"}
                                ),
                                flush=True,
                            )
                    finally:
                        stop.set()
                        if writer:
                            writer.join(timeout=30)
                            if writer.is_alive():
                                raise RuntimeError("Ingestion did not stop")
                        report.setdefault("ingestion", []).append(
                            dict(rows=rows, concurrent=concurrent, batches=writes)
                        )
                    if any("error" in batch for batch in writes):
                        raise RuntimeError(f"Ingestion failed: {writes[-1]}")
        report["completed_at"] = datetime.now(UTC).isoformat()
        write_report(output, report)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
