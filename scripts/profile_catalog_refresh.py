"""Measure catalog-refresh starvation with real SQL on a disposable local database.

Uses the production-size fixture in profile_read_models. --seed replaces all data.
The report gate is tested with an explicitly aged refresh timestamp so a fast
developer machine reproduces a scan older than the 60-second freshness budget.
All measured durations use the real clock; the injected age is reported separately.
"""

import argparse
import json
import math
import os
import platform
import queue
import resource
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from profile_read_models import (
    ISOLATED_ENV,
    ROOT,
    dataset_counts,
    dataset_resources,
    fixture_counts,
    fixture_id,
    parity,
    seed_fixture,
    validate_targets,
)
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import sessionmaker

REPORT_GROUPS = frozenset({"operations"})


def measure(operation):
    before = resource.getrusage(resource.RUSAGE_SELF)
    started = time.perf_counter()
    result = operation()
    after = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "wall_seconds": round(time.perf_counter() - started, 4),
        "application_cpu_seconds": round(
            after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime, 4
        ),
        "result": result,
    }


class SQLSamples:
    """Capture one real invocation; EXPLAIN only SELECTs on the disposable target."""

    def __init__(self, engine):
        self.engine = engine
        self.queries = {}
        self.local = threading.local()

    def before(self, _connection, _cursor, statement, parameters, _context, many):
        self.local.started = time.perf_counter()

    def after(self, _connection, _cursor, statement, parameters, _context, many):
        compiled = getattr(getattr(_context, "compiled", None), "statement", None)
        is_select = statement.lstrip().upper().startswith("SELECT") or getattr(
            compiled, "is_select", False
        )
        if is_select and any(
            name in statement
            for name in ("pipeline_projections", "read_model_events", "article_analysis_jobs")
        ):
            sample = self.queries.setdefault(
                statement,
                {"statement": statement, "parameters": parameters, "calls": 0, "sql_seconds": 0},
            )
            sample["calls"] += 1
            sample["sql_seconds"] += time.perf_counter() - self.local.started

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self.before)
        event.listen(self.engine, "after_cursor_execute", self.after)
        return self

    def __exit__(self, *_args):
        event.remove(self.engine, "before_cursor_execute", self.before)
        event.remove(self.engine, "after_cursor_execute", self.after)

    def explain(self):
        plans = []
        with self.engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL statement_timeout='30s'")
            for sample in self.queries.values():
                plan = connection.exec_driver_sql(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sample["statement"],
                    sample["parameters"],
                ).scalar_one()[0]
                plans.append(
                    {
                        "statement": sample["statement"],
                        "calls": sample["calls"],
                        "sql_seconds": round(sample["sql_seconds"], 4),
                        "plan": plan,
                    }
                )
        return plans


def state(factory):
    from devfeed_core.models import ReadModelEvent, ReadModelState, ReportSnapshot

    with factory() as session:
        pipeline = session.get(ReadModelState, "pipeline")
        return {
            "pipeline": pipeline.payload if pipeline else None,
            "pending_events": session.scalar(select(func.count()).select_from(ReadModelEvent)),
            "catalog_cursors": [
                str(value)
                for value in session.scalars(
                    select(ReadModelEvent.after_id).where(
                        ReadModelEvent.kind == "catalog", ReadModelEvent.after_id.is_not(None)
                    )
                )
            ],
            "reports": [
                {"group": row.group, "days": row.days, "generated_at": row.generated_at.isoformat()}
                for row in session.scalars(select(ReportSnapshot))
            ],
        }


def database_activity(factory):
    with factory() as session:
        return dict(
            session.execute(
                text(
                    "SELECT blks_read,blks_hit,temp_files,temp_bytes,tup_inserted,tup_updated,"
                    "tup_deleted,pg_current_wal_lsn()::text AS server_wal_lsn "
                    "FROM pg_stat_database WHERE datname=current_database()"
                )
            )
            .mappings()
            .one()
        )


def publish(factory):
    """Measure the bounded operational section during PG catalog refreshes."""
    from devfeed_admin_api import overview_projection as reports
    from devfeed_admin_api.overview_windows import current_window

    with factory.begin() as session:
        reports.request_refresh(session, "operations", current_window(), datetime.now(UTC))
    now = datetime.now(UTC)
    claim = reports.claim_refresh(factory, now)
    if claim is None:
        return []
    try:
        result = reports.calculate_report(factory, claim[0], claim[1], now)
        accepted = reports.publish_report(factory, claim, result, datetime.now(UTC))
        return [
            {
                "group": claim[0],
                "days": claim[1].days,
                "published": accepted,
                "snapshot": result.snapshot.model_dump(mode="json") if result.snapshot else None,
            }
        ]
    except Exception as error:
        reports.fail_refresh(factory, claim, datetime.now(UTC), error)
        return [
            {
                "group": claim[0],
                "days": claim[1].days,
                "published": False,
                "error": type(error).__name__,
            }
        ]


def mutate(factory, requests, completed, stats):
    """A separate connection commits ordinary ingestion and catalog edits together."""
    from devfeed_core.models import Article, ArticleAnalysisJob, ArticleOrigin, IngestionJob, Topic

    while (number := requests.get()) is not None:
        started = time.perf_counter()
        try:
            with factory.begin() as session:
                topic = session.get(Topic, fixture_id("topics", 1))
                topic.description = "Concurrent refresh benchmark revision " + str(stats["commits"])
                identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"catalog-profile-ingestion-{number}")
                session.add(
                    Article(
                        id=identifier,
                        canonical_url="https://benchmark.example/" + str(identifier),
                        url_hash=identifier.hex,
                        title="Concurrent benchmark article",
                        summary="Synthetic evidence for ingestion during a catalog refresh.",
                    )
                )
                session.flush()
                session.add(
                    ArticleOrigin(
                        article_id=identifier,
                        source_id=fixture_id("sources", 1),
                        entry_key=str(identifier),
                        original_url="https://benchmark.example/" + str(identifier),
                    )
                )
                session.add(ArticleAnalysisJob(article_id=identifier, status="queued"))
                ingestion = IngestionJob(source_id=fixture_id("sources", 1), status="succeeded")
                session.add(ingestion)
            stats["commits"] += 1
            stats["wall_seconds"] += time.perf_counter() - started
            stats.setdefault("article_ids", []).append(str(identifier))
            stats.setdefault("ingestion_job_ids", []).append(str(ingestion.id))
        except Exception as error:
            stats["error"] = type(error).__name__ + ": " + str(error)
        finally:
            completed.release()


def gates(output, expected):
    samples = output["continuous"]
    reports = [result for sample in samples for result in sample["reports"]["result"]]
    stable = output["stable_reports"]["result"]
    snapshots = [result["snapshot"] for result in reports if result.get("published")]
    generations = [row.get("article_generation") for row in snapshots]
    generations_valid = all(type(value) is int and value >= 0 for value in generations)
    checks = {
        "stable_reports_publish": len(stable) == 1
        and {row["group"] for row in stable} == REPORT_GROUPS
        and all(row.get("published") for row in stable),
        "writer_commits_match_batches": output["concurrent_ingestion"]["commits"] == len(samples)
        and not output["concurrent_ingestion"].get("error"),
        "converges_after_writer_stops": output["final_state"]["pending_events"] == 0
        and not output["final_state"]["pipeline"].get("catalog_refresh"),
        "publication_generations_do_not_regress": generations_valid
        and generations == sorted(generations),
        "converged_cards_match_authoritative_lifecycle": output["legacy_lifecycle_parity"][
            "matches_legacy_lifecycle"
        ],
        "catalog_cursor_advances": len(
            {cursor for sample in samples for cursor in sample["state"]["catalog_cursors"]}
        )
        > 1,
    }
    baseline = {row["group"] for row in reports} == REPORT_GROUPS and all(
        row.get("published") is False and row.get("error") == "ProjectionNotReady"
        for row in reports
    )
    fixed_publication = bool(samples) and all(
        len(sample["reports"]["result"]) == 1
        and {row["group"] for row in sample["reports"]["result"]} == REPORT_GROUPS
        and all(row.get("published") for row in sample["reports"]["result"])
        for sample in samples
    )
    catching_up = bool(snapshots) and all(
        row["status"] == "catching_up"
        and type(row.get("article_generation")) is int
        and row["article_generation"] >= 0
        and type(row.get("lag_seconds")) in {int, float}
        and math.isfinite(row["lag_seconds"])
        and 0 <= row["lag_seconds"] <= 60
        and type(row.get("catalog_lag_seconds")) in {int, float}
        and math.isfinite(row["catalog_lag_seconds"])
        and row["catalog_lag_seconds"] >= 60
        for row in snapshots
    )
    if expected == "baseline":
        checks["reproduces_report_starvation"] = baseline
    elif expected == "fixed":
        checks["publishes_every_requested_report"] = fixed_publication
        checks["reports_explicitly_catch_up"] = catching_up
    else:
        checks["recognizes_baseline_or_fixed_behavior"] = baseline or (
            fixed_publication and catching_up
        )
    if output["workload"]["require_native_arm64"]:
        checks["native_arm64_postgresql_17"] = output["qualification"]["native_arm64_postgresql_17"]
    return {
        "expected_behavior": expected,
        "observed_behavior": "baseline"
        if baseline
        else "fixed"
        if fixed_publication and catching_up
        else "unrecognized",
        "checks": checks,
        "passed": all(checks.values()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--history-bytes", type=int, default=8192)
    parser.add_argument("--batch", type=int, default=200)
    parser.add_argument("--batches", type=int, default=20)
    parser.add_argument("--age-refresh-seconds", type=int, default=61)
    parser.add_argument("--expect", choices=("baseline", "fixed", "either"), default="fixed")
    parser.add_argument("--require-native-arm64", action="store_true")
    parser.add_argument("--environment-note", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        url = validate_targets(
            os.environ["DEVFEED_TEST_DATABASE_URL"], os.environ["DEVFEED_TEST_REDIS_URL"]
        )
        counts = fixture_counts(args.scale)
        if not (
            1 <= args.batch <= 1000
            and 1 <= args.batches <= 10000
            and 0 <= args.age_refresh_seconds <= 3600
            and 128 <= args.history_bytes <= 65536
        ):
            raise ValueError("Profiling arguments exceed bounded workload limits")
    except (ValueError, KeyError) as error:
        parser.error(str(error))
    os.environ.update(
        DEVFEED_DATABASE_URL=url.render_as_string(hide_password=False),
        DEVFEED_REDIS_URL=os.environ["DEVFEED_TEST_REDIS_URL"],
        DEVFEED_AI_CONTENT_NOT_BEFORE="",
        DEVFEED_IMAGE_STORAGE_ENABLED="false",
        DEVFEED_CACHE_ENABLED="false",
        DEVFEED_METRICS_ENABLED="false",
        DEVFEED_OTLP_ENDPOINT="",
        **ISOLATED_ENV,
    )
    from devfeed_admin_api.pipeline_projection import drain_pipeline, project_batch
    from devfeed_core.config import get_settings
    from devfeed_core.db import create_database_engine
    from devfeed_core.models import ArticleEnrichmentJob, PipelineProjection, ReadModelState
    from devfeed_core.read_models import enqueue_event

    get_settings.cache_clear()
    engine = create_database_engine(get_settings())
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        postgres_version = session.scalar(select(func.version()))
    native_arm64_postgresql_17 = (
        platform.machine().lower() in {"arm64", "aarch64"}
        and any(architecture in postgres_version.lower() for architecture in ("arm64", "aarch64"))
        and "PostgreSQL 17." in postgres_version
    )
    if args.require_native_arm64 and not native_arm64_postgresql_17:
        engine.dispose()
        parser.error("Native ARM64 Python and PostgreSQL 17 are required")
    output = {
        "complete": False,
        "hardware": {
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "postgres_version": postgres_version,
            "cpu_count": os.cpu_count(),
            "load_average_start": os.getloadavg(),
            "note": args.environment_note,
        },
        "scope": "Disposable PG catalog/pipeline/operations benchmark; CH analytics excluded",
        "workload": vars(args)
        | {"output": str(args.output), "report_groups": sorted(REPORT_GROUPS)},
    }

    def checkpoint(phase):
        output["phase"] = phase
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2, default=str) + "\n")
        print(json.dumps({"phase": phase}), file=sys.stderr, flush=True)

    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        if args.seed:
            output["seed"] = seed_fixture(engine, counts, history_bytes=args.history_bytes)
        if args.seed or args.prepare_only:
            # Sequential comparisons can exceed the fixture's original 30-minute
            # retry timer. Preserve retry-wait examples beyond the bounded run so
            # both cloned databases face the same transitions, regardless of speed.
            from sqlalchemy import update

            prepared_at = datetime.now(UTC)
            deadline = prepared_at + timedelta(hours=3)
            with factory.begin() as session:
                changed = session.execute(
                    update(ArticleEnrichmentJob)
                    .where(
                        ArticleEnrichmentJob.status == "queued",
                        ArticleEnrichmentJob.available_at > prepared_at,
                    )
                    .values(available_at=deadline)
                ).rowcount
            output["prepared_retry_timers"] = {
                "queued_enrichment_jobs": changed,
                "available_at": deadline.isoformat(),
            }
        output["bootstrap"] = measure(lambda: drain_pipeline(factory, batch=args.batch))
        output["dataset"] = dataset_counts(factory)
        with factory() as session:
            output["dataset"]["retained_article_cards"] = session.scalar(
                select(func.count())
                .select_from(PipelineProjection)
                .where(PipelineProjection.kind == "article")
            )
        if args.prepare_only:
            output["prepared"] = True
            checkpoint("prepared")
            return 0
        with SQLSamples(engine) as stable_sql:
            output["stable_reports"] = measure(lambda: publish(factory))
        output["stable_query_plans"] = stable_sql.explain()
        checkpoint("stable")
        with factory.begin() as session:
            enqueue_event(session, "catalog")
        project_batch(factory, batch=args.batch)
        with factory.begin() as session:
            row = session.get(ReadModelState, "pipeline")
            payload = dict(row.payload)
            refresh = dict(payload["catalog_refresh"])
            refresh["started_at"] = (
                datetime.now(UTC) - timedelta(seconds=args.age_refresh_seconds)
            ).isoformat()
            payload["catalog_refresh"] = refresh
            row.payload = payload
        requests = queue.Queue()
        completed = threading.Semaphore(0)
        writer_stats = {"commits": 0, "wall_seconds": 0}
        writer = threading.Thread(
            target=mutate,
            args=(factory, requests, completed, writer_stats),
            daemon=True,
        )
        writer.start()
        output["database_activity_before"] = database_activity(factory)
        try:
            samples = []
            with SQLSamples(engine) as sql:
                for number in range(args.batches):
                    requests.put(number)
                    project = measure(lambda: project_batch(factory, batch=args.batch))
                    if not completed.acquire(timeout=30):
                        raise RuntimeError(
                            "Concurrent writer did not finish within its bounded wait"
                        )
                    samples.append(
                        {
                            "project": project,
                            "reports": measure(lambda: publish(factory)),
                            "state": state(factory),
                        }
                    )
                    output["continuous"] = samples
                    checkpoint("continuous")
            output["query_plans"] = sql.explain()
        finally:
            requests.put(None)
            writer.join(timeout=30)
        output["concurrent_ingestion"] = writer_stats
        output["convergence"] = measure(lambda: drain_pipeline(factory, batch=args.batch))
        output["final_state"] = state(factory)
        output["legacy_lifecycle_parity"] = parity(factory)
        output["database_activity_after"] = database_activity(factory)
        output["database_activity_delta"] = {
            name: value - output["database_activity_before"][name]
            for name, value in output["database_activity_after"].items()
            if name != "server_wal_lsn"
        }
        with factory() as session:
            output["server_wal_bytes"] = int(
                session.scalar(
                    text("SELECT pg_wal_lsn_diff(CAST(:after AS pg_lsn),CAST(:before AS pg_lsn))"),
                    {
                        "after": output["database_activity_after"]["server_wal_lsn"],
                        "before": output["database_activity_before"]["server_wal_lsn"],
                    },
                )
            )
        output["resources"] = dataset_resources(factory)
        output["hardware"]["load_average_end"] = os.getloadavg()
        output["qualification"] = {
            "meets_issue_article_population": output["dataset"]["articles"] >= 100000,
            "issue_production_articles": 92000,
            "issue_production_retained_cards": 19679,
            "native_arm64_postgresql_17": native_arm64_postgresql_17,
        }
        output["gates"] = gates(output, args.expect)
        output["complete"] = True
        checkpoint("complete")
        return int(not output["gates"]["passed"])
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
