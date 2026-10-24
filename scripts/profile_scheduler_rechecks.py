"""Profile repeated scheduler rechecks and real ingestion on disposable fixtures.

Uses profile_read_models' fixed historical populations, ordinary database triggers,
the article automation portion of a scheduler tick, the real feed-entry writer,
and the real pipeline publisher. Both configured services must be explicitly local;
--seed replaces the configured *_test database. No provider or feed network calls
are made. Run the same script in base/head checkouts on the same native machine.
"""

import argparse
import contextvars
import json
import os
import platform
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import profile_read_models as fixture
from alembic import command
from alembic.config import Config
from devfeed_core import models
from sqlalchemy import event, func, select, text, update
from sqlalchemy.orm import sessionmaker

PHASE = contextvars.ContextVar("scheduler_profile_phase", default="setup")
PRODUCTION_2026_10_24 = {
    "articles": 92581,
    "article_analysis_jobs": 156004,
    "article_enrichment_jobs": 104577,
    "article_reviews": 255716,
    "article_origins": 95307,
    "sources": 1406,
    "topics": 4450,
    "tags": 49165,
    "article_topics": 352280,
    "article_tags": 930387,
    "queued_analysis": 7302,
    "running_analysis": 4,
}


def validate_arguments(args):
    counts = fixture.fixture_counts(args.scale)
    # Even 100k articles must have historical/taxonomy cardinalities above the
    # fresh incident sample, rather than scaling those below production.
    counts.update(
        {
            name: max(1, int(total * args.scale))
            for name, total in {
                "article_analysis_jobs": 300000,
                "article_enrichment_jobs": 160000,
                "article_contents": 150000,
                "article_reviews": 450000,
                "topics": 7500,
                "tags": 90000,
                "article_topics": 600000,
                "article_tags": 1500000,
                "sources": 2250,
            }.items()
        }
    )
    if (
        not 1 <= args.ticks <= 20
        or not 1 <= args.batch <= 50
        or not 1 <= args.ingest_per_tick <= 50
        or not 128 <= args.history_bytes <= 65536
    ):
        raise ValueError(
            "Supported bounds: ticks1..20, batch1..50, ingest-per-tick1..50, "
            "history-bytes128..65536"
        )
    if counts["articles"] < 40 * args.batch:
        raise ValueError("Fixture must contain at least 40 articles per scheduler candidate")
    return counts


@contextmanager
def phase(name):
    token = PHASE.set(name)
    try:
        yield
    finally:
        PHASE.reset(token)


def classify_plan(statement, phase_name):
    normalized = statement.lower()
    if phase_name == "scheduler":
        if (
            "from articles" in normalized
            and "order by articles.automation_next_check_at" in normalized
        ):
            return "scheduler_due_candidates"
        if "from article_analysis_jobs" in normalized:
            return "scheduler_latest_analysis"
        if normalized.startswith("update articles") and "automation_next_check_at" in normalized:
            return "scheduler_clock_update"
    if (
        phase_name == "publisher"
        and "from article_analysis_jobs" in normalized
        and "catalog_snapshot" in normalized
    ):
        return "publisher_analysis_hydration"
    return None


class Recorder:
    def __init__(self, engine):
        self.queries = Counter()
        self.written_cards = Counter()
        self.plans = {}
        self.engine = engine

    def before(self, connection, cursor, statement, parameters, context, executemany):
        name = PHASE.get()
        self.queries[name] += 1
        label = classify_plan(statement, name)
        if label is not None and label not in self.plans and not executemany:
            self.plans[label] = (statement, parameters)

    def after(self, connection, cursor, statement, parameters, context, executemany):
        if statement.lower().startswith("insert into pipeline_projections"):
            self.written_cards[PHASE.get()] += max(0, cursor.rowcount)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self.before)
        event.listen(self.engine, "after_cursor_execute", self.after)
        return self

    def __exit__(self, *_):
        event.remove(self.engine, "before_cursor_execute", self.before)
        event.remove(self.engine, "after_cursor_execute", self.after)

    def explain(self, *, identifier, next_check):
        results = {}
        for label, (statement, parameters) in self.plans.items():
            # EXPLAIN ANALYZE runs writes too; always roll the sample back.
            with self.engine.connect() as connection, connection.begin() as transaction:
                connection.exec_driver_sql("SET LOCAL statement_timeout='30s'")
                plan = connection.exec_driver_sql(
                    "EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON) " + statement, parameters
                ).scalar_one()
                transaction.rollback()
            results[label] = {"statement": statement, "plan": plan}
        # Show the trigger's keyed projection access separately: PostgreSQL's
        # outer UPDATE plan reports trigger time without nested PL/pgSQL plans.
        with self.engine.connect() as connection, connection.begin() as transaction:
            state = connection.execute(
                text("SELECT payload FROM read_model_states WHERE name='pipeline'")
            ).scalar_one()
            statement = """
                UPDATE pipeline_projections SET payload=jsonb_set(
                    payload,'{next_check_at}',to_jsonb(CAST(:next_check AS timestamptz)),true)
                WHERE policy_version=ANY(CAST(:policies AS text[])) AND kind='article'
                  AND entity_id=:identifier AND state<>'complete'
            """
            plan = connection.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON) " + statement),
                {
                    "next_check": next_check,
                    "identifier": identifier,
                    "policies": [state.get("target_policy"), state.get("serving_policy")],
                },
            ).scalar_one()
            transaction.rollback()
        results["proposed_clock_projection_patch"] = {"statement": statement, "plan": plan}
        return results


def statement_totals(engine):
    with engine.connect() as connection:
        return dict(
            connection.execute(
                text("""
            SELECT coalesce(sum(calls),0)::bigint AS calls,
                   coalesce(sum(total_exec_time),0) AS sql_elapsed_ms,
                   coalesce(sum(shared_blks_hit),0)::bigint AS shared_blks_hit,
                   coalesce(sum(shared_blks_read),0)::bigint AS shared_blks_read,
                   coalesce(sum(shared_blks_dirtied),0)::bigint AS shared_blks_dirtied,
                   coalesce(sum(shared_blks_written),0)::bigint AS shared_blks_written,
                   coalesce(sum(temp_blks_read),0)::bigint AS temp_blks_read,
                   coalesce(sum(temp_blks_written),0)::bigint AS temp_blks_written,
                   coalesce(sum(wal_bytes),0)::bigint AS wal_bytes
            FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname=current_database())
              AND toplevel
              AND query NOT LIKE '%pg_stat_statements%'
        """)
            )
            .mappings()
            .one()
        )


def prepare_queue(factory, *, batch, now, backlog):
    """Prepare a fixed pending cohort; historical payloads and triggers stay intact."""
    identifiers = [fixture.fixture_id("articles", 20 + 40 * index) for index in range(batch)]
    with factory.begin() as session:
        # Fixture-wide setup is outside the bounded scheduler workload. Keep the
        # application's per-statement budget for the measured ticks themselves.
        session.execute(text("SET LOCAL statement_timeout='0'"))
        session.execute(
            update(models.ArticleAnalysisJob)
            .where(models.ArticleAnalysisJob.status.in_(("queued", "running")))
            .values(status="succeeded", finished_at=now)
        )
        session.execute(
            text("""
                WITH pending AS (
                    SELECT id FROM articles WHERE review_status='pending'
                      AND publication_status='unpublished' ORDER BY id LIMIT :backlog
                ), latest AS (
                    SELECT DISTINCT ON (j.article_id) j.id
                    FROM article_analysis_jobs j JOIN pending p ON p.id=j.article_id
                    ORDER BY j.article_id,j.created_at DESC,j.id DESC
                )
                UPDATE article_analysis_jobs j SET status='queued',available_at=:available,
                    lease_until=NULL,finished_at=NULL FROM latest WHERE latest.id=j.id
            """),
            {"backlog": backlog, "available": now + timedelta(days=1)},
        )
        session.execute(
            update(models.Article).values(automation_next_check_at=now + timedelta(days=2))
        )
        session.execute(
            update(models.Article)
            .where(models.Article.id.in_(identifiers))
            .values(
                review_status="pending",
                publication_status="unpublished",
                automation_started_at=now - timedelta(hours=1),
                automation_next_check_at=now,
            )
        )
        session.execute(
            update(models.ArticleOrigin)
            .where(models.ArticleOrigin.article_id.in_(identifiers))
            .values(source_id=fixture.fixture_id("sources", 1))
        )
        for index, identifier in enumerate(identifiers):
            job = session.scalar(
                select(models.ArticleAnalysisJob)
                .where(models.ArticleAnalysisJob.article_id == identifier)
                .order_by(
                    models.ArticleAnalysisJob.created_at.desc(), models.ArticleAnalysisJob.id.desc()
                )
                .limit(1)
            )
            job.status = "running" if index < 4 else "queued"
            job.available_at = now + timedelta(days=1)
            job.lease_until = now + timedelta(days=1) if index < 4 else None
            job.finished_at = None
    return identifiers


def active_jobs(factory, identifiers):
    with factory() as session:
        return [
            tuple(row)
            for row in session.execute(
                select(
                    models.ArticleAnalysisJob.id,
                    models.ArticleAnalysisJob.status,
                    models.ArticleAnalysisJob.attempts,
                    models.ArticleAnalysisJob.lease_until,
                    models.ArticleAnalysisJob.available_at,
                    models.ArticleAnalysisJob.lease_token,
                    models.ArticleAnalysisJob.dispatched_at,
                )
                .where(
                    models.ArticleAnalysisJob.article_id.in_(identifiers),
                    models.ArticleAnalysisJob.status.in_(("queued", "running")),
                )
                .order_by(models.ArticleAnalysisJob.id)
            )
        ]


def prepare_projections(factory, identifiers, now):
    """Keep real index cardinality without benchmarking an unrelated full backfill.

    Unmeasured cards are synthetic placeholders. The measured waiting cohort is
    rebuilt by the real publisher before sampling, and all measured ingests use
    ordinary capture and publication. This is not a full-backfill benchmark.
    """
    from devfeed_core.read_models import enqueue_event, pipeline_policy_version, set_state

    policy = pipeline_policy_version()
    with factory.begin() as session:
        session.execute(text("SET LOCAL statement_timeout='0'"))
        session.execute(text("TRUNCATE pipeline_projections, read_model_events, read_model_states"))
        session.execute(
            text("""
            INSERT INTO pipeline_projections
              (policy_version,kind,entity_id,payload,stage,state,search_text,created_at,
               activity_at,terminal_at,has_active_work,next_transition_at,evaluated_at)
            SELECT :policy,'article',id,jsonb_build_object('_profile_placeholder',true),
                   CASE WHEN publication_status='published' THEN 'published'
                        WHEN review_status='rejected' THEN 'rejected' ELSE 'analysis' END,
                   'waiting',title,discovered_at,discovered_at,
                   CASE WHEN publication_status='published' THEN published_to_feed_at END,
                   false,NULL,:now FROM articles
        """),
            {"policy": policy, "now": now},
        )
        set_state(
            session,
            "pipeline",
            {
                "target_policy": policy,
                "serving_policy": policy,
                "pending_backfills": [],
                "backfill_complete": True,
                "backfill_started_at": now.isoformat(),
                "obsolete_policies": [],
                "serving_generated_at": now.isoformat(),
            },
        )
        for identifier in identifiers:
            enqueue_event(session, "article", identifier)
        session.execute(text("ANALYZE pipeline_projections"))


def ingest(factory, *, tick, count, now, started):
    from devfeed_aggregator.tasks import store_entries
    from devfeed_core.feeds.parser import ParsedArticle, ParsedFeed
    from devfeed_core.source_types import SourceType

    begin, cpu = time.perf_counter(), time.thread_time()
    started.set()
    entries = [
        ParsedArticle(
            entry_key=f"scheduler-profile-{tick}-{index}",
            canonical_url=f"https://publisher.example/scheduler-profile/{tick}/{index}",
            title=f"Python runtime ingestion {tick} {index}",
            summary="Python coroutine evidence from concurrent feed ingestion. " * 10,
            author=None,
            published_at=now,
            tags=[],
            source_type=SourceType.PUBLISHER,
            feed_at=now,
            source_metadata={},
            language="en",
        )
        for index in range(count)
    ]
    with phase("ingestion"), factory.begin() as session:
        created = store_entries(
            session, fixture.fixture_id("sources", 1), ParsedFeed(entries, count, 0)
        )
        identifiers = list(
            session.scalars(
                select(models.Article.id).where(
                    models.Article.canonical_url.in_([entry.canonical_url for entry in entries])
                )
            )
        )
        # Isolate the fixed analysis cohort from fresh enrichment work this tick.
        session.execute(
            update(models.Article)
            .where(models.Article.id.in_(identifiers))
            .values(automation_next_check_at=now + timedelta(days=2))
        )
    return {
        "created": created,
        "identifiers": identifiers,
        "started": begin,
        "finished": time.perf_counter(),
        "cpu_seconds": time.thread_time() - cpu,
    }


def drain_at(factory, now, batch=200):
    from devfeed_admin_api.pipeline_projection import project_batch
    from devfeed_core.analysis import reuse_candidates
    from devfeed_core.catalog_cache import reuse_snapshots

    count = 0
    with reuse_snapshots(), reuse_candidates():
        for _ in range(10000):
            processed = project_batch(factory, batch=batch, now=now)
            count += processed
            if not processed:
                with factory() as session:
                    if session.scalar(select(models.ReadModelEvent.id).limit(1)) is not None:
                        raise RuntimeError("Publisher stopped with pending events")
                return count
    raise RuntimeError("Publisher exceeded its bounded drain")


def cohort_visibility(factory, identifiers):
    with factory() as session:
        cards = session.execute(
            select(
                models.Article.id,
                models.Article.automation_next_check_at,
                models.PipelineProjection.payload,
                models.PipelineProjection.next_transition_at,
            )
            .join(
                models.PipelineProjection, models.PipelineProjection.entity_id == models.Article.id
            )
            .where(models.Article.id.in_(identifiers), models.PipelineProjection.kind == "article")
        ).all()
        queued = dict(
            session.execute(
                select(
                    models.ArticleAnalysisJob.article_id, models.ArticleAnalysisJob.available_at
                ).where(
                    models.ArticleAnalysisJob.article_id.in_(identifiers),
                    models.ArticleAnalysisJob.status == "queued",
                )
            ).all()
        )
    clocks = len(cards) == len(identifiers)
    transitions = len(cards) == len(identifiers)
    for identifier, authoritative, payload, transition in cards:
        shown = payload.get("next_check_at")
        clocks = clocks and shown is not None and datetime.fromisoformat(shown) == authoritative
        if identifier in queued:
            transitions = transitions and transition == queued[identifier]
    return {"cohort_clock_visible": clocks, "cohort_retry_transition_correct": transitions}


def run_ticks(factory, engine, *, ticks, batch, ingest_per_tick, identifiers, now):
    from devfeed_admin_api import pipeline_projection
    from devfeed_core import article_automation

    results, hydration, cohort = [], Counter(), set(identifiers)
    original = pipeline_projection.article_rows

    def rows(factory, identifiers, policy, at):
        hydration["article_ids"] += len(identifiers)
        hydration["cohort_ids"] += len(cohort.intersection(identifiers))
        hydration["batches"] += 1
        return original(factory, identifiers, policy, at)

    initial_jobs = active_jobs(factory, identifiers)
    with (
        Recorder(engine) as recorder,
        patch.object(pipeline_projection, "article_rows", rows),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        for tick in range(ticks):
            at = now + (tick + 1) * (article_automation.CHECK_INTERVAL + timedelta(seconds=1))
            previous_queries, previous_writes = (
                recorder.queries.copy(),
                recorder.written_cards.copy(),
            )
            previous_hydration = hydration.copy()
            started = threading.Event()
            writer = pool.submit(
                ingest, factory, tick=tick, count=ingest_per_tick, now=at, started=started
            )
            if not started.wait(10):
                raise RuntimeError("Concurrent ingestion did not start")
            begin, cpu = time.perf_counter(), time.thread_time()
            with phase("scheduler"), patch.object(article_automation, "utcnow", return_value=at):
                outcome = article_automation.schedule_article_automation(factory)
            scheduler_end = time.perf_counter()
            scheduler_cpu = time.thread_time() - cpu
            ingested = writer.result(timeout=120)
            with factory() as session:
                cohort_events = session.scalar(
                    select(func.count())
                    .select_from(models.ReadModelEvent)
                    .where(models.ReadModelEvent.entity_id.in_(identifiers))
                )
                pending_events = session.scalar(
                    select(func.count()).select_from(models.ReadModelEvent)
                )
            publisher_begin, publisher_cpu = time.perf_counter(), time.thread_time()
            with phase("publisher"):
                acknowledged = drain_at(factory, at)
            publisher_seconds = time.perf_counter() - publisher_begin
            publisher_cpu = time.thread_time() - publisher_cpu
            with factory() as session:
                projected = session.scalar(
                    select(func.count())
                    .select_from(models.PipelineProjection)
                    .where(
                        models.PipelineProjection.kind == "article",
                        models.PipelineProjection.entity_id.in_(ingested["identifiers"]),
                    )
                )
            result = {
                "tick": tick + 1,
                "outcome": outcome,
                "scheduler_seconds": scheduler_end - begin,
                "scheduler_cpu_seconds": scheduler_cpu,
                "publisher_seconds": publisher_seconds,
                "publisher_cpu_seconds": publisher_cpu,
                "ingestion_seconds": ingested["finished"] - ingested["started"],
                "ingestion_cpu_seconds": ingested["cpu_seconds"],
                "ingestion_overlapped_scheduler": ingested["started"] < scheduler_end
                and ingested["finished"] > begin,
                "ingested": ingested["created"],
                "ingested_projected": projected,
                "pending_events_before_publish": pending_events,
                "cohort_events": cohort_events,
                "publisher_acknowledged": acknowledged,
                "hydration": dict(hydration - previous_hydration),
                "sql_statements": dict(recorder.queries - previous_queries),
                "publisher_upsert_rows": dict(recorder.written_cards - previous_writes),
                "active_analysis_unchanged": active_jobs(factory, identifiers) == initial_jobs,
                **cohort_visibility(factory, identifiers),
            }
            results.append(result)
            print(json.dumps({"stage": "tick", **result}), flush=True)
    return results, recorder


def bounded_failures(ticks, *, batch, ingest_per_tick):
    failures = []
    for tick in ticks:
        reasons = []
        if tick["outcome"]["articles_checked"] != batch:
            reasons.append("fixed candidate batch was not checked")
        if tick["outcome"]["articles_published"] or tick["outcome"]["articles_rejected"]:
            reasons.append("waiting analysis acquired an editorial outcome")
        if tick["cohort_events"] or tick["hydration"].get("cohort_ids", 0):
            reasons.append("clock-only rechecks generated card events or hydration")
        if tick["ingested"] != ingest_per_tick or tick["ingested_projected"] != ingest_per_tick:
            reasons.append("genuine concurrent ingestion was not projected")
        if not tick["ingestion_overlapped_scheduler"]:
            reasons.append("ingestion did not overlap scheduler work")
        if not tick["active_analysis_unchanged"]:
            reasons.append("active analysis lease, retry, or identity changed")
        if not tick["cohort_clock_visible"] or not tick["cohort_retry_transition_correct"]:
            reasons.append("current recheck clock or durable retry transition is incorrect")
        if reasons:
            failures.append({"tick": tick["tick"], "reasons": reasons})
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--ticks", type=int, default=5)
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--ingest-per-tick", type=int, default=10)
    parser.add_argument("--history-bytes", type=int, default=8192)
    parser.add_argument("--label", default="candidate")
    parser.add_argument("--assert-bounded", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        url = fixture.validate_targets(
            os.environ["DEVFEED_TEST_DATABASE_URL"], os.environ["DEVFEED_TEST_REDIS_URL"]
        )
        counts = validate_arguments(args)
    except (KeyError, ValueError) as error:
        parser.error(str(error))
    os.environ.update(fixture.ISOLATED_ENV)
    os.environ.update(
        DEVFEED_DATABASE_URL=url.render_as_string(hide_password=False),
        DEVFEED_READ_MODEL_DATABASE_URL=url.render_as_string(hide_password=False),
        DEVFEED_REDIS_URL=os.environ["DEVFEED_TEST_REDIS_URL"],
        DEVFEED_CACHE_ENABLED="false",
        DEVFEED_AI_CONTENT_NOT_BEFORE="",
        DEVFEED_AI_ENABLED="true",
        DEVFEED_FULL_AUTOMATION="true",
        DEVFEED_CODEX_APP_SERVER_URL="ws://127.0.0.1:9",
        DEVFEED_CODEX_MODEL="profile-unused",
        DEVFEED_IMAGE_STORAGE_ENABLED="false",
        DEVFEED_METRICS_ENABLED="false",
        DEVFEED_OTLP_ENDPOINT="",
        DEVFEED_AUTOMATION_BATCH_SIZE=str(args.batch),
        DEVFEED_DATABASE_POOL_ENABLED="true",
        DEVFEED_DATABASE_POOL_SIZE="4",
    )
    from devfeed_core import article_automation
    from devfeed_core.config import Settings, get_settings
    from devfeed_core.db import create_database_engine, get_engine

    # A checkout's .env must never supply provider, database, or Redis routing.
    Settings.model_config = dict(Settings.model_config, env_file=None)
    get_settings.cache_clear()
    get_engine.cache_clear()
    engine = create_database_engine(get_settings())
    factory = sessionmaker(engine, expire_on_commit=False)
    output = {
        "complete": False,
        "label": args.label,
        "started_at": datetime.now(UTC).isoformat(),
        "hardware": {
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "workload": {
            "scale": args.scale,
            "ticks": args.ticks,
            "batch": args.batch,
            "ingest_per_tick": args.ingest_per_tick,
            "history_bytes": args.history_bytes,
        },
        "production_2026_10_24_0928_utc": PRODUCTION_2026_10_24,
        "scope": "Synthetic warmed local PostgreSQL; real article automation, feed-entry writer, "
        "capture triggers and publisher. Excludes other scheduler services and provider calls, "
        "production infrastructure and proxy. Clock advances by 301 seconds per iteration.",
    }

    def checkpoint(stage):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2, default=str) + "\n")
        print(json.dumps({"stage": stage, "output": str(args.output)}), flush=True)

    try:
        command.upgrade(Config(str(fixture.ROOT / "alembic.ini")), "head")
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_stat_statements"))
        if args.seed:
            output["seed"] = fixture.seed_fixture(engine, counts, history_bytes=args.history_bytes)
            checkpoint("seed")
        now = datetime.now(UTC)
        identifiers = prepare_queue(
            factory, batch=args.batch, now=now, backlog=max(args.batch, int(12000 * args.scale))
        )
        with patch.object(article_automation, "utcnow", return_value=now):
            output["warmup_scheduler"] = article_automation.schedule_article_automation(factory)
        prepare_projections(factory, identifiers, now)
        output["projection_setup"] = (
            "Synthetic placeholder cards preserve full article index cardinality; "
            "the measured waiting cohort and every measured ingest use real card derivation. "
            "This excludes full-population backfill performance."
        )
        started = time.perf_counter()
        output["initial_publisher_events"] = drain_at(factory, now)
        output["initial_publisher_seconds"] = time.perf_counter() - started
        output["dataset"] = fixture.dataset_counts(factory)
        with factory() as session:
            for status in ("queued", "running"):
                output["dataset"][status + "_analysis"] = session.scalar(
                    select(func.count())
                    .select_from(models.ArticleAnalysisJob)
                    .where(models.ArticleAnalysisJob.status == status)
                )
        output["below_production_population"] = {
            name: {"actual": output["dataset"].get(name, 0), "production": count}
            for name, count in PRODUCTION_2026_10_24.items()
            if output["dataset"].get(name, 0) < count
        }
        output["resources"] = fixture.dataset_resources(factory)
        checkpoint("prepared")
        before = statement_totals(engine)
        started, cpu = time.perf_counter(), time.process_time()
        output["ticks"], recorder = run_ticks(
            factory,
            engine,
            ticks=args.ticks,
            batch=args.batch,
            ingest_per_tick=args.ingest_per_tick,
            identifiers=identifiers,
            now=now,
        )
        output["workload_seconds"] = time.perf_counter() - started
        output["application_cpu_seconds"] = time.process_time() - cpu
        after = statement_totals(engine)
        output["postgres_workload_delta"] = {key: after[key] - before[key] for key in before}
        output["plans"] = recorder.explain(
            identifier=identifiers[0], next_check=now + timedelta(hours=1)
        )
        output["bounded_failures"] = bounded_failures(
            output["ticks"], batch=args.batch, ingest_per_tick=args.ingest_per_tick
        )
        output["complete"] = True
        checkpoint("complete")
        if args.assert_bounded and output["bounded_failures"]:
            raise SystemExit("Scheduler benchmark failed its bounded-work assertions")
    finally:
        checkpoint("final")
        engine.dispose()


if __name__ == "__main__":
    main()
