"""Sustained production scheduler/publisher workload against disposable local services.

Uses the production scheduler and publisher loops, including proactive report demand.
Source fetches and inference providers are replaced by synthetic committed feed input;
no external worker or provider is started. Results distinguish smoke from native scale.
"""

import argparse
import asyncio
import hashlib
import json
import math
import multiprocessing
import os
import platform
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import profile_read_models as fixture
from alembic import command
from alembic.config import Config
from sqlalchemy import func, literal, or_, select, text, update
from sqlalchemy.orm import sessionmaker

MAINTENANCE_WAIT_REASONS = (
    "maintenance",
    "dependencies",
    "source_admission",
    "content_date_deferred",
)


def workload_environment(database_url, redis_url, article_batch_size=None):
    """Run AI admission and automation; only inference execution stays absent."""
    values = {
        "DEVFEED_DATABASE_URL": database_url,
        "DEVFEED_READ_MODEL_DATABASE_URL": database_url,
        "DEVFEED_REDIS_URL": redis_url,
        "DEVFEED_CACHE_ENABLED": "false",
        "DEVFEED_AI_ENABLED": "true",
        "DEVFEED_FULL_AUTOMATION": "true",
        "DEVFEED_CODEX_APP_SERVER_URL": "ws://127.0.0.1:1",
        "DEVFEED_CODEX_MODEL": "benchmark",
        "DEVFEED_CODEX_AUTH_TOKEN": "",
        "DEVFEED_AI_QUOTA_PACING_ENABLED": "false",
        "DEVFEED_AUTOMATION_BATCH_SIZE": "50",
        "DEVFEED_AUTOMATION_DEPENDENCY_WAKES_ENABLED": "true",
        "DEVFEED_ARTICLE_AUTOMATION_CYCLE_SECONDS": "40",
        "DEVFEED_IMAGE_STORAGE_ENABLED": "false",
        "DEVFEED_NOTIFICATIONS_ENABLED": "false",
        "DEVFEED_METRICS_ENABLED": "false",
        "DEVFEED_OTLP_ENDPOINT": "",
    }
    if article_batch_size is not None:
        values["DEVFEED_ARTICLE_AUTOMATION_BATCH_SIZE"] = str(article_batch_size)
    return values


def scheduler_configuration(settings):
    article_batch = getattr(settings, "article_automation_batch_size", None)
    wakes = getattr(settings, "automation_dependency_wakes_enabled", False)
    return {
        "ai_enabled": settings.ai_enabled,
        "full_automation": settings.full_automation,
        "ai_quota_pacing_enabled": settings.ai_quota_pacing_enabled,
        "codex_endpoint": settings.codex_app_server_url,
        "codex_model": settings.codex_model,
        "provider_workers_started": False,
        "automation_batch_size": settings.automation_batch_size,
        "article_automation_batch_size": article_batch,
        "article_automation_effective_batch_size": article_batch or settings.automation_batch_size,
        "article_automation_cycle_seconds": (
            getattr(settings, "article_automation_cycle_seconds", None)
            if article_batch is not None and wakes
            else None
        ),
        "automation_dependency_wakes_enabled": wakes,
    }


def worktree_changes():
    entries = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=fixture.ROOT,
        text=True,
    ).split("\0")
    paths = []
    index = 0
    while index < len(entries) and entries[index]:
        entry = entries[index]
        paths.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            index += 1
            paths.append(entries[index])
        index += 1
    return sorted(set(paths))


def worker(kind, log_path):
    # Spawn owns its signal handlers, engines, health markers and log descriptors.
    with open(log_path, "a", encoding="utf-8") as stream:
        os.dup2(stream.fileno(), 1)
        os.dup2(stream.fileno(), 2)
        from devfeed_core.config import Settings, get_settings

        Settings.model_config = dict(Settings.model_config, env_file=None)
        get_settings.cache_clear()
        if kind == "scheduler":
            from devfeed_aggregator.scheduler import run

            run()
        else:
            from devfeed_admin_api.projection_worker import run

            run(kind=kind)


def stop_worker(process):
    if process.is_alive():
        os.kill(process.pid, signal.SIGTERM)
    process.join(timeout=15)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
        raise RuntimeError("A production worker failed to stop within its grace period")
    if process.exitcode != 0:
        raise RuntimeError(f"Worker failed with exit code {process.exitcode}")


def statement_snapshot(factory):
    with factory() as session:
        rows = session.execute(
            text(
                "SELECT queryid,calls,total_exec_time,shared_blks_hit,shared_blks_read,"
                "temp_blks_written,wal_bytes,query FROM pg_stat_statements "
                "WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database()) "
                "AND toplevel"
            )
        ).mappings()
        result = {str(row["queryid"]): dict(row) for row in rows}
        result["__dealloc__"] = dict(
            session.execute(
                text(
                    "SELECT dealloc AS count,stats_reset,"
                    "pg_postmaster_start_time() AS server_started "
                    "FROM pg_stat_statements_info"
                )
            )
            .mappings()
            .one()
        )
        return result


def statement_delta(before, after):
    if after.get("__dealloc__") != before.get("__dealloc__"):
        raise RuntimeError("Statement statistics were evicted during the workload")
    fields = (
        "calls",
        "total_exec_time",
        "shared_blks_hit",
        "shared_blks_read",
        "temp_blks_written",
        "wal_bytes",
    )
    if set(before) - set(after):
        raise RuntimeError("Statement statistics disappeared during the workload")
    result = {field: 0.0 for field in fields}
    for key, row in after.items():
        if key == "__dealloc__":
            continue
        previous = before.get(key, {})
        for field in fields:
            change = float(row[field]) - float(previous.get(field, 0))
            if change < 0:
                raise RuntimeError("Statement statistics were reset during the workload")
            result[field] += change
    return result


def process_snapshot(pid):
    # The final parenthesis terminates comm, which can itself contain spaces.
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {
        "pid": pid,
        "started": int(fields[19]),
        "cpu_seconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
        "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
    }


def process_samples(processes):
    return {
        "driver_and_readers": process_snapshot(os.getpid()),
        **{kind: process_snapshot(process.pid) for kind, process in processes.items()},
    }


def process_delta(before, after, peaks):
    result = {}
    for kind, row in after.items():
        previous = before[kind]
        if (row["pid"], row["started"]) != (previous["pid"], previous["started"]):
            raise RuntimeError("A measured process was replaced during the phase")
        consumed = row["cpu_seconds"] - previous["cpu_seconds"]
        if consumed < 0:
            raise RuntimeError("Process CPU counters decreased")
        result[kind] = {
            "cpu_seconds": consumed,
            "sampled_peak_rss_bytes": max(peaks.get(kind, 0), row["rss_bytes"]),
        }
    return result


def database_snapshot(factory):
    """Per-database activity plus explicitly global, isolated-cluster maintenance cost."""
    with factory() as session:
        session.execute(text("SELECT pg_stat_clear_snapshot()"))
        database = dict(
            session.execute(
                text(
                    "SELECT xact_commit,xact_rollback,blks_read,blks_hit,tup_inserted,tup_updated,"
                    "tup_deleted,temp_files,temp_bytes,deadlocks,blk_read_time,blk_write_time,"
                    "stats_reset "
                    "FROM pg_stat_database WHERE datname=current_database()"
                )
            )
            .mappings()
            .one()
        )
        wal = dict(
            session.execute(
                text(
                    "SELECT wal_records,wal_fpi,wal_bytes,wal_buffers_full,wal_write,wal_sync,"
                    "wal_write_time,wal_sync_time,stats_reset FROM pg_stat_wal"
                )
            )
            .mappings()
            .one()
        )
        io = dict(
            session.execute(
                text(
                    "SELECT coalesce(sum(reads),0) AS reads,coalesce(sum(writes),0) AS writes,"
                    "coalesce(sum(fsyncs),0) AS fsyncs,coalesce(sum(hits),0) AS hits,"
                    "coalesce(sum(evictions),0) AS evictions,min(stats_reset) AS stats_reset "
                    "FROM pg_stat_io"
                )
            )
            .mappings()
            .one()
        )
        maintenance = dict(
            session.execute(
                text(
                    "SELECT coalesce(sum(autovacuum_count),0) AS autovacuums,"
                    "coalesce(sum(autoanalyze_count),0) AS autoanalyses,"
                    "coalesce(sum(n_dead_tup),0) AS estimated_dead_tuples "
                    "FROM pg_stat_user_tables"
                )
            )
            .mappings()
            .one()
        )
        lsn = session.scalar(text("SELECT pg_current_wal_lsn()::text"))
        high, low = lsn.split("/")
        return {
            "database": database,
            "global_wal": wal,
            "global_io": io,
            "maintenance": maintenance,
            "wal_position": (int(high, 16) << 32) + int(low, 16),
        }


def database_delta(before, after):
    result = {}
    for group in ("database", "global_wal", "global_io", "maintenance"):
        previous, current = before[group], after[group]
        if current.get("stats_reset") != previous.get("stats_reset"):
            raise RuntimeError("Database/IO/WAL statistics were reset during measurement")
        result[group] = {}
        for name, value in current.items():
            if name == "stats_reset":
                continue
            change = float(value) - float(previous[name])
            if change < 0 and name != "estimated_dead_tuples":
                raise RuntimeError("Database/IO/WAL statistics decreased during measurement")
            result[group][name] = change
    result["global_wal_position_bytes"] = after["wal_position"] - before["wal_position"]
    if result["global_wal_position_bytes"] < 0:
        raise RuntimeError("PostgreSQL WAL position regressed")
    return result


class ConcurrentReaders:
    """Collect actual individual API latencies while the writer and publishers run."""

    def __init__(self, factory, *, interval=5):
        self.factory, self.interval = factory, interval
        self.stopped = threading.Event()
        self.records, self.error = [], None
        self.thread = threading.Thread(target=self.run, name="background-api-readers", daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        try:
            asyncio.run(self.sample())
        except BaseException as error:
            self.error = error

    async def sample(self):
        application = dict(zip(("admin", "partner"), fixture.apps(self.factory), strict=True))
        clients = {
            name: httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://profile.test",
                cookies=app.state.profile_cookies,
            )
            for name, app in application.items()
        }
        semaphore = asyncio.Semaphore(8)

        async def request(target):
            label, service, url = target
            async with semaphore:
                started = time.perf_counter()
                response = await asyncio.wait_for(clients[service].get(url), timeout=30)
                self.records.append(
                    (label, (time.perf_counter() - started) * 1000, response.status_code)
                )

        try:
            while not self.stopped.is_set():
                started = time.monotonic()
                await asyncio.gather(*(request(target) for target in fixture.workload()))
                self.stopped.wait(max(0, self.interval - (time.monotonic() - started)))
        finally:
            for client in clients.values():
                await client.aclose()
            for app in application.values():
                redis, session_key = app.state.profile_session
                redis.delete(session_key)

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=60)
        if self.thread.is_alive():
            raise RuntimeError("Concurrent API readers did not finish within their grace period")
        if self.error is not None:
            raise RuntimeError("Concurrent API reader failed") from self.error
        groups = defaultdict(list)
        for label, milliseconds, status in self.records:
            groups[label].append((milliseconds, status))
        return {
            label: {
                "samples": len(rows),
                "p50_ms": fixture.percentile([row[0] for row in rows], 0.5),
                "p95_ms": fixture.percentile([row[0] for row in rows], 0.95),
                "p99_ms": fixture.percentile([row[0] for row in rows], 0.99),
                "status_counts": dict(Counter(row[1] for row in rows)),
            }
            for label, rows in groups.items()
        }


def backlog(factory):
    from devfeed_core.models import ReadModelEvent, ReadModelState, ReportRefresh

    now = datetime.now(UTC)
    with factory() as session:
        count, oldest = session.execute(
            select(func.count(), func.min(ReadModelEvent.created_at))
            .select_from(ReadModelEvent)
            .where(ReadModelEvent.kind != "catalog")
        ).one()
        catalog_count, catalog_oldest = session.execute(
            select(func.count(), func.min(ReadModelEvent.created_at)).where(
                ReadModelEvent.kind == "catalog"
            )
        ).one()
        pipeline = session.get(ReadModelState, "pipeline")
        refresh = (pipeline.payload if pipeline else {}).get("catalog_refresh")
        progress = (refresh or {}).get("progress_at") or (refresh or {}).get("started_at")
        progress_at = datetime.fromisoformat(progress) if progress else catalog_oldest
        started = (refresh or {}).get("started_at")
        catchup_at = datetime.fromisoformat(started) if started else catalog_oldest
        result = {
            "events": count,
            "catalog_running": bool(refresh),
            "catalog_progress_age_seconds": max(0, (now - progress_at).total_seconds())
            if progress_at
            else 0,
            "catalog_catchup_age_seconds": max(0, (now - catchup_at).total_seconds())
            if catchup_at
            else 0,
            "catalog_events": catalog_count,
            "oldest_catalog_seconds": max(0, (now - catalog_oldest).total_seconds())
            if catalog_oldest
            else 0,
            "oldest_event_seconds": max(0, (now - oldest).total_seconds()) if oldest else 0,
            "reports": session.scalar(select(func.count()).select_from(ReportRefresh)),
            "report_errors": session.scalar(
                select(func.count())
                .select_from(ReportRefresh)
                .where(ReportRefresh.last_error.is_not(None))
            ),
        }
        from devfeed_core import models

        dirty_model = getattr(models, "ReportingDirty", None)
        if dirty_model is not None:
            dirty_count, dirty_oldest = session.execute(
                select(func.count(), func.min(dirty_model.created_at)).select_from(dirty_model)
            ).one()
            result.update(
                fact_events=dirty_count,
                oldest_fact_seconds=max(0, (now - dirty_oldest).total_seconds())
                if dirty_oldest
                else 0,
            )
        result.update(automation_backlog(session, now))
        return result


def automation_backlog(session, now):
    from devfeed_core import models

    article = models.Article
    reason = getattr(article, "automation_wait_reason", None)
    maintenance = reason.in_(MAINTENANCE_WAIT_REASONS) if reason is not None else literal(False)
    foreground = or_(reason.is_(None), ~maintenance) if reason is not None else literal(True)
    due = article.automation_next_check_at <= now
    rows = (
        session.execute(
            select(
                func.count().filter(due & foreground).label("automation_foreground_due"),
                func.min(article.automation_next_check_at)
                .filter(due & foreground)
                .label("foreground_oldest"),
                func.count().filter(due & maintenance).label("automation_maintenance_due"),
                func.min(article.automation_next_check_at)
                .filter(due & maintenance)
                .label("maintenance_oldest"),
                func.count().filter(~due).label("automation_waiting"),
            ).where(
                article.review_status.in_(["pending", "approved"]),
                article.publication_status == "unpublished",
            )
        )
        .mappings()
        .one()
    )
    result = dict(rows)
    for lane in ("foreground", "maintenance"):
        oldest = result.pop(lane + "_oldest")
        result[f"oldest_automation_{lane}_due_seconds"] = (
            max(0, (now - oldest).total_seconds()) if oldest else 0
        )
    invalidation = getattr(models, "AutomationInvalidation", None)
    result.update(
        automation_invalidations_supported=invalidation is not None,
        automation_invalidations=0,
        automation_foreground_invalidations=0,
        automation_maintenance_invalidations=0,
        automation_invalidation_progress=[],
        oldest_automation_foreground_invalidation_seconds=0,
        oldest_automation_maintenance_invalidation_seconds=0,
        automation_foreground_invalidation_progress_age_seconds=0,
        automation_maintenance_invalidation_progress_age_seconds=0,
    )
    if invalidation is None:
        return result
    progress = func.coalesce(invalidation.progressed_at, invalidation.requested_at)
    foreground_demand = or_(invalidation.source_id.is_not(None), invalidation.key.like("article:%"))
    tag_id = getattr(invalidation, "tag_id", None)
    if tag_id is not None:
        foreground_demand = or_(foreground_demand, tag_id.is_not(None))
    columns = [func.count().label("automation_invalidations")]
    for lane, condition in (
        ("foreground", foreground_demand),
        ("maintenance", ~foreground_demand),
    ):
        columns.extend(
            [
                func.count().filter(condition).label(f"automation_{lane}_invalidations"),
                func.min(invalidation.requested_at).filter(condition).label(lane + "_oldest"),
                func.min(progress).filter(condition).label(lane + "_progress"),
            ]
        )
    values = dict(session.execute(select(*columns)).mappings().one())
    for lane in ("foreground", "maintenance"):
        for suffix, key in (
            ("oldest", f"oldest_automation_{lane}_invalidation_seconds"),
            ("progress", f"automation_{lane}_invalidation_progress_age_seconds"),
        ):
            timestamp = values.pop(lane + "_" + suffix)
            result[key] = max(0, (now - timestamp).total_seconds()) if timestamp else 0
    result.update(values)
    cursor_columns = [
        invalidation.key,
        invalidation.source_id,
        invalidation.revision,
        invalidation.scan_revision,
        invalidation.after_article_id,
        invalidation.requested_at,
        invalidation.progressed_at,
    ]
    if tag_id is not None:
        cursor_columns.append(tag_id)
    result["automation_invalidation_progress"] = [
        dict(row)
        for row in session.execute(
            select(*cursor_columns).order_by(invalidation.key).limit(100)
        ).mappings()
    ]
    return result


def convergence_lanes(state):
    """Fanout completion is insufficient while the articles it woke remain due."""
    foreground = all(
        state.get(key, 0) == 0
        for key in (
            "events",
            "fact_events",
            "reports",
            "report_errors",
            "automation_foreground_invalidations",
            "automation_foreground_due",
        )
    )
    maintenance = not state.get("catalog_running", False) and all(
        state.get(key, 0) == 0
        for key in (
            "catalog_events",
            "automation_maintenance_invalidations",
            "automation_maintenance_due",
        )
    )
    return {"foreground": foreground, "maintenance": maintenance}


def observe_convergence(state, completed_at, elapsed):
    """Only the final uninterrupted empty interval satisfies a lane's deadline."""
    lanes = convergence_lanes(state)
    for lane, empty in lanes.items():
        if not empty:
            completed_at[lane] = None
        elif completed_at[lane] is None:
            completed_at[lane] = elapsed
    return lanes


def convergence_outcome(state, completed_at, limits):
    lanes = convergence_lanes(state)
    results = {
        lane: {
            "passed": lanes[lane]
            and completed_at[lane] is not None
            and completed_at[lane] <= limit,
            "limit_seconds": limit,
            "cleared_at_seconds": completed_at[lane],
            "final_empty": lanes[lane],
        }
        for lane, limit in limits.items()
    }
    return {"passed": all(row["passed"] for row in results.values()), "lanes": results}


def prepare_automation_fixture(factory):
    """Represent pre-migration historical debt; new synthetic arrivals stay foreground."""
    from devfeed_core.models import Article

    reason = getattr(Article, "automation_wait_reason", None)
    if reason is None:
        return 0
    with factory.begin() as session:
        return session.execute(
            update(Article)
            .where(
                Article.review_status.in_(["pending", "approved"]),
                Article.publication_status == "unpublished",
            )
            .values(automation_wait_reason="maintenance")
        ).rowcount


def ingest(factory, count):
    from devfeed_aggregator.tasks import store_entries
    from devfeed_core.feeds.parser import ParsedArticle, ParsedFeed
    from devfeed_core.source_types import SourceType

    now, token = datetime.now(UTC), uuid.uuid4().hex
    entries = [
        ParsedArticle(
            entry_key=f"background-{token}-{index}",
            canonical_url=f"https://publisher.example/background/{token}/{index}",
            title=f"Python runtime workload {token} {index}",
            summary="Python coroutine evidence for background workload. " * 10,
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
    started = time.monotonic()
    with factory.begin() as session:
        created = store_entries(
            session, fixture.fixture_id("sources", 1), ParsedFeed(entries, count, 0)
        )
    return created, (time.monotonic() - started) * 1000


def touch_reports(factory):
    from devfeed_admin_api import overview_projection as reports
    from devfeed_core.models import ReportSnapshot

    now = datetime.now(UTC)
    with factory.begin() as session:
        # The publisher seeds the same refresh keys in lexical order. Multi-key
        # demand transactions must match that order to avoid insert deadlocks.
        for group in sorted(reports.REPORT_GROUPS):
            for days in (7, 30, 90):
                touch = getattr(reports, "touch_report_demand", None)
                if touch is not None:
                    touch(session, group, days, now)
                key = reports.report_key(group, days)
                stored = session.get(ReportSnapshot, key)
                freshness = getattr(reports, "report_fresh_seconds", lambda _group: 60)(group)
                if (
                    stored is None
                    or stored.policy_version != reports.report_policy_version()
                    or stored.generated_at <= now - timedelta(seconds=freshness)
                ):
                    reports.request_refresh(session, group, days, now)


def reports_ready(factory):
    from devfeed_admin_api.overview_projection import (
        REPORT_GROUPS,
        report_key,
        report_policy_version,
    )
    from devfeed_core.models import ReportSnapshot

    required = {report_key(group, days) for group in REPORT_GROUPS for days in (7, 30, 90)}
    with factory() as session:
        return all(
            (stored := session.get(ReportSnapshot, key)) is not None
            and stored.policy_version == report_policy_version()
            for key in required
        )


def prepare_projections(factory):
    """Explicit disposable fixture setup, measured separately from sustained work."""
    from devfeed_admin_api.pipeline_projection import drain_pipeline

    started = time.monotonic()
    events = drain_pipeline(factory)
    facts = 0
    # The same harness also runs on the base commit, before fact tables existed.
    try:
        from devfeed_core.reporting_facts import (
            ReportingFactsUnavailable,
            refresh_reporting_facts,
            require_reporting_facts,
        )
    except ModuleNotFoundError as error:
        if error.name != "devfeed_core.reporting_facts":
            raise
        return {"seconds": time.monotonic() - started, "events": events, "facts": 0}
    for _ in range(20000):
        processed = refresh_reporting_facts(factory, limit=200)
        facts += processed
        if processed == 0:
            with factory() as session:
                try:
                    require_reporting_facts(session)
                except ReportingFactsUnavailable:
                    # An empty source kind still advances its coverage cursor;
                    # later kinds can contain work even when this page did not.
                    continue
            return {"seconds": time.monotonic() - started, "events": events, "facts": facts}
    raise RuntimeError("Reporting fixture preparation failed to converge")


def catalog_edit(factory):
    from devfeed_core.models import Tag

    with factory.begin() as session:
        identifier = session.scalar(select(Tag.id).order_by(Tag.id).limit(1))
        session.execute(
            update(Tag)
            .where(Tag.id == identifier)
            .values(aliases=["background-" + uuid.uuid4().hex])
        )


def gates(
    phases,
    *,
    reader_p95_ms,
    writer_p95_ms,
    max_sql_seconds_per_minute,
    max_buffers_per_minute,
    max_wal_bytes_per_minute,
    max_event_age_seconds,
    max_cpu_seconds_per_minute=120,
    max_rss_bytes=2147483648,
    max_global_wal_bytes_per_minute=134217728,
    max_maintenance_age_seconds=14400,
):
    failures = []
    if not phases:
        failures.append("No measured phases")
    for phase in phases:
        name = phase["name"]
        minutes = phase["seconds"] / 60
        if minutes <= 0:
            failures.append(name + ": empty phase")
            continue
        checks = {
            "SQL duty": phase["cost"]["total_exec_time"] / 1000 / minutes
            <= max_sql_seconds_per_minute,
            "buffer work": (phase["cost"]["shared_blks_read"] + phase["cost"]["shared_blks_hit"])
            / minutes
            <= max_buffers_per_minute,
            "WAL": phase["cost"]["wal_bytes"] / minutes <= max_wal_bytes_per_minute,
            "event age": phase["backlog"]["oldest_event_seconds"] <= max_event_age_seconds,
        }
        checks["catalog progress age"] = (
            phase["backlog"].get("catalog_progress_age_seconds", 0) <= max_event_age_seconds
        )
        checks["fact age"] = phase["backlog"].get("oldest_fact_seconds", 0) <= max_event_age_seconds
        checks["automation backlog instrumentation"] = all(
            key in phase["backlog"]
            for key in (
                "automation_foreground_due",
                "automation_maintenance_due",
                "automation_foreground_invalidations",
                "automation_maintenance_invalidations",
            )
        )
        checks["foreground automation age"] = (
            max(
                phase["backlog"].get("oldest_automation_foreground_due_seconds", 0),
                phase["backlog"].get("oldest_automation_foreground_invalidation_seconds", 0),
            )
            <= max_event_age_seconds
        )
        checks["automation fanout progress"] = (
            max(
                phase["backlog"].get("automation_foreground_invalidation_progress_age_seconds", 0),
                phase["backlog"].get("automation_maintenance_invalidation_progress_age_seconds", 0),
            )
            <= max_event_age_seconds
        )
        checks["maintenance automation age"] = (
            max(
                phase["backlog"].get("oldest_automation_maintenance_due_seconds", 0),
                phase["backlog"].get("oldest_automation_maintenance_invalidation_seconds", 0),
                phase["backlog"].get("catalog_catchup_age_seconds", 0),
            )
            <= max_maintenance_age_seconds
        )
        resources = phase.get("processes")
        checks["CPU instrumentation"] = bool(resources)
        checks["database maintenance instrumentation"] = bool(phase.get("database_cost"))
        if resources:
            checks["Python CPU"] = (
                sum(row["cpu_seconds"] for row in resources.values()) / minutes
                <= max_cpu_seconds_per_minute
            )
            checks["sampled process RSS"] = (
                max(row["sampled_peak_rss_bytes"] for row in resources.values()) <= max_rss_bytes
            )
        if phase.get("database_cost"):
            checks["global WAL including maintenance"] = (
                phase["database_cost"]["global_wal_position_bytes"] / minutes
                <= max_global_wal_bytes_per_minute
            )
        if name in {"active", "recovery"}:
            checks["concurrent readers observed"] = bool(phase.get("readers"))
            checks["writer samples observed"] = bool(phase.get("writer_ms"))
            checks["offered input completed"] = phase["ingested"] == phase["planned_ingested"]
        if phase.get("writer_ms"):
            checks["writer p95"] = fixture.percentile(phase["writer_ms"], 0.95) <= writer_p95_ms
        for label, row in phase.get("readers", {}).items():
            checks[label + " p95"] = row["p95_ms"] <= reader_p95_ms
            checks[label + " errors"] = {int(code) for code in row["status_counts"]} == {200}
        failures.extend(name + ": " + label for label, passed in checks.items() if not passed)
    return {"passed": not failures, "failures": failures}


def write_result(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replace a directory entry; never follow a pre-existing destination link.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".background-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(result, stream, indent=2, default=str)
            stream.write("\n")
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--history-bytes", type=int, default=8192)
    parser.add_argument("--active-seconds", type=int, default=1800)
    parser.add_argument("--idle-seconds", type=int, default=1260)
    parser.add_argument("--idle-grace-seconds", type=int, default=660)
    parser.add_argument("--recovery-seconds", type=int, default=180)
    parser.add_argument("--convergence-seconds", type=int, default=900)
    parser.add_argument("--maintenance-convergence-seconds", type=int, default=14400)
    parser.add_argument("--warmup-seconds", type=int, default=900)
    parser.add_argument("--interval-seconds", type=float, default=5)
    parser.add_argument("--ingest-per-interval", type=int, default=5)
    parser.add_argument("--article-batch-size", type=int)
    parser.add_argument("--sql-seconds-per-minute", type=float, required=True)
    parser.add_argument("--buffers-per-minute", type=float, required=True)
    parser.add_argument("--wal-bytes-per-minute", type=float, required=True)
    parser.add_argument("--cpu-seconds-per-minute", type=float, default=120)
    parser.add_argument("--rss-bytes", type=int, default=2147483648)
    parser.add_argument("--global-wal-bytes-per-minute", type=float, default=134217728)
    parser.add_argument("--isolated-database-cluster", action="store_true")
    parser.add_argument("--reader-p95-ms", type=float, default=1000)
    parser.add_argument("--writer-p95-ms", type=float, default=1000)
    parser.add_argument("--max-event-age-seconds", type=float, default=60)
    parser.add_argument("--require-native-arm64", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        url = fixture.validate_targets(
            os.environ["DEVFEED_TEST_DATABASE_URL"], os.environ["DEVFEED_TEST_REDIS_URL"]
        )
        counts = fixture.fixture_counts(args.scale)
        if args.article_batch_size is not None and not 1 <= args.article_batch_size <= 500:
            raise ValueError("Article batch size must be 1..500")
        if not 128 <= args.history_bytes <= 65536 or not 1 <= args.ingest_per_interval <= 100:
            raise ValueError("Invalid history size or ingest count")
        if any(
            not 1 <= value <= 86400
            for value in (
                args.active_seconds,
                args.idle_seconds,
                args.recovery_seconds,
                args.warmup_seconds,
                args.convergence_seconds,
                args.maintenance_convergence_seconds,
            )
        ):
            raise ValueError("Phase durations must be 1..86400 seconds")
        if not 0 <= args.idle_grace_seconds <= 86400:
            raise ValueError("Idle grace must be 0..86400 seconds")
        if not 1 <= args.interval_seconds <= 60:
            raise ValueError("Interval must be 1..60 seconds")
        budgets = (
            args.sql_seconds_per_minute,
            args.buffers_per_minute,
            args.wal_bytes_per_minute,
            args.reader_p95_ms,
            args.writer_p95_ms,
            args.max_event_age_seconds,
            args.cpu_seconds_per_minute,
            args.rss_bytes,
            args.global_wal_bytes_per_minute,
        )
        if any(not 0 < value < float("inf") for value in budgets):
            raise ValueError("All budgets must be finite and positive")
        if args.require_native_arm64 and platform.machine() not in {"aarch64", "arm64"}:
            raise ValueError("Native ARM64 is required")
    except (KeyError, ValueError) as error:
        parser.error(str(error))
    os.environ.update(fixture.AUTH_ENV)
    os.environ.update(fixture.ISOLATED_ENV)
    os.environ.pop("DEVFEED_ARTICLE_AUTOMATION_BATCH_SIZE", None)
    os.environ.update(
        workload_environment(
            url.render_as_string(hide_password=False),
            os.environ["DEVFEED_TEST_REDIS_URL"],
            args.article_batch_size,
        )
    )
    from devfeed_core.config import Settings, get_settings
    from devfeed_core.db import create_database_engine

    Settings.model_config = dict(Settings.model_config, env_file=None)
    get_settings.cache_clear()
    engine = create_database_engine(get_settings())
    factory = sessionmaker(engine, expire_on_commit=False)
    processes = {}
    changes = worktree_changes()
    output = {
        "complete": False,
        "architecture": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "phases": [],
        "workload": vars(args),
        "qualification": "smoke",
        "scheduler_configuration": scheduler_configuration(get_settings()),
        "scope": "Production scheduler/publisher loops with concurrent synthetic ingestion and "
        "authenticated ASGI admin/partner readers; no external fetching/inference workers, "
        "OIDC exchange, PgBouncer/proxies or browsers. CPU covers scheduler, publishers and "
        "driver/readers; RSS is sampled, not kernel peak. PostgreSQL global WAL/IO include "
        "maintenance and require an isolated local cluster. Buffer reads can hit the OS cache.",
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=fixture.ROOT, text=True
        ).strip(),
        "worktree_dirty": bool(changes),
        "source_worktree_changes": changes,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    context = multiprocessing.get_context("spawn")

    def start(kind):
        path = args.output.parent / (args.output.stem + "." + kind + ".log")
        process = context.Process(target=worker, args=(kind, str(path)))
        process.start()
        processes[kind] = process

    try:
        command.upgrade(Config(str(fixture.ROOT / "alembic.ini")), "head")
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_stat_statements"))
            output["postgresql"] = dict(
                connection.execute(
                    text(
                        "SELECT current_setting('server_version_num') AS version_num,"
                        "current_setting('shared_buffers') AS shared_buffers,"
                        "current_setting('work_mem') AS work_mem,"
                        "current_setting('track_io_timing') AS track_io_timing,"
                        "current_setting('max_connections') AS max_connections"
                    )
                )
                .mappings()
                .one()
            )
        if args.seed:
            fixture.seed_fixture(engine, counts, history_bytes=args.history_bytes)
        preparation_sql = statement_snapshot(factory)
        preparation_db = database_snapshot(factory)
        preparation_cpu = process_snapshot(os.getpid())
        output["fixture_preparation"] = prepare_projections(factory)
        output["fixture_preparation"]["historical_automation_maintenance_rows"] = (
            prepare_automation_fixture(factory) if args.seed else 0
        )
        output["fixture_preparation"].update(
            cost=statement_delta(preparation_sql, statement_snapshot(factory)),
            database_cost=database_delta(preparation_db, database_snapshot(factory)),
            driver_cpu_seconds=process_snapshot(os.getpid())["cpu_seconds"]
            - preparation_cpu["cpu_seconds"],
        )
        output["dataset"] = fixture.dataset_counts(factory)
        if not fixture.dataset_qualification(output["dataset"], counts)[
            "meets_requested_population"
        ]:
            raise RuntimeError("Fixture does not match requested population")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        for kind in ("scheduler", "pipeline", "reports"):
            start(kind)
        touch_reports(factory)
        deadline = time.monotonic() + args.warmup_seconds
        warmed = False
        next_demand = time.monotonic() + 5
        while time.monotonic() < deadline:
            if time.monotonic() >= next_demand:
                touch_reports(factory)
                next_demand = time.monotonic() + 5
            state = backlog(factory)
            if state["events"] == 0 and reports_ready(factory):
                warmed = True
                break
            if any(not process.is_alive() for process in processes.values()):
                raise RuntimeError("Worker failed during warmup")
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        if not warmed:
            raise RuntimeError("Initial coverage/reports did not converge before warmup deadline")
        output["warmup_backlog"] = state
        for name, duration in (
            ("active", args.active_seconds),
            ("idle", args.idle_seconds),
            ("recovery", args.recovery_seconds),
        ):
            if name == "recovery":
                # Hard exit tests lease recovery and replay of captured changes.
                for kind in ("pipeline", "reports"):
                    processes[kind].kill()
                    processes[kind].join(timeout=5)
                    start(kind)
            before = statement_snapshot(factory)
            db_before, cpu_before = database_snapshot(factory), process_samples(processes)
            started = time.monotonic()
            phase = {
                "name": name,
                "writer_ms": [],
                "ingested": 0,
                "planned_ingested": 0
                if name == "idle"
                else math.ceil(duration / args.interval_seconds) * args.ingest_per_interval,
            }
            next_edit = started
            peaks = {kind: row["rss_bytes"] for kind, row in cpu_before.items()}
            tail_before = None
            tail_peaks = {}
            reader = (
                ConcurrentReaders(factory, interval=args.interval_seconds)
                if name != "idle"
                else None
            )
            if reader:
                reader.start()
            try:
                while time.monotonic() - started < duration:
                    cycle = time.monotonic()
                    if any(not process.is_alive() for process in processes.values()):
                        raise RuntimeError("A production worker exited during measurement")
                    if reader and reader.error is not None:
                        raise RuntimeError("Concurrent readers failed") from reader.error
                    for kind, row in process_samples(processes).items():
                        peaks[kind] = max(peaks[kind], row["rss_bytes"])
                        if tail_before is not None:
                            tail_peaks[kind] = max(tail_peaks.get(kind, 0), row["rss_bytes"])
                    if (
                        name == "idle"
                        and tail_before is None
                        and cycle - started >= args.idle_grace_seconds
                    ):
                        tail_before = (
                            statement_snapshot(factory),
                            database_snapshot(factory),
                            process_samples(processes),
                            time.monotonic(),
                        )
                    if name != "idle":
                        created, elapsed = ingest(factory, args.ingest_per_interval)
                        phase["ingested"] += created
                        phase["writer_ms"].append(elapsed)
                        touch_reports(factory)
                        if cycle >= next_edit:
                            catalog_edit(factory)
                            next_edit = cycle + 60
                    time.sleep(
                        max(
                            0,
                            min(
                                args.interval_seconds - (time.monotonic() - cycle),
                                duration - (time.monotonic() - started),
                            ),
                        )
                    )
            finally:
                phase["readers"] = reader.stop() if reader else {}
            phase["seconds"] = time.monotonic() - started
            after = statement_snapshot(factory)
            db_after, cpu_after = database_snapshot(factory), process_samples(processes)
            phase["cost"] = statement_delta(before, after)
            phase["database_cost"] = database_delta(db_before, db_after)
            phase["processes"] = process_delta(cpu_before, cpu_after, peaks)
            phase["backlog"] = backlog(factory)
            output["phases"].append(phase)
            if tail_before:
                tail_sql, tail_db, tail_cpu, tail_started = tail_before
                output["phases"].append(
                    {
                        "name": "idle-expired",
                        "seconds": time.monotonic() - tail_started,
                        "cost": statement_delta(tail_sql, after),
                        "database_cost": database_delta(tail_db, db_after),
                        "processes": process_delta(tail_cpu, cpu_after, tail_peaks),
                        "backlog": phase["backlog"],
                        "readers": {},
                        "writer_ms": [],
                        "ingested": 0,
                    }
                )
            write_result(args.output, output)
        # Producers/readers have stopped. Charge all remaining catchup and
        # maintenance work to a separate measured drain instead of hiding it.
        drain_before = statement_snapshot(factory)
        drain_db, drain_cpu = database_snapshot(factory), process_samples(processes)
        drain_started = time.monotonic()
        drain_peaks = {kind: row["rss_bytes"] for kind, row in drain_cpu.items()}
        converged = False
        limits = {
            "foreground": args.convergence_seconds,
            "maintenance": args.maintenance_convergence_seconds,
        }
        completed_at = {lane: None for lane in limits}
        drain_limit = max(limits.values())
        while True:
            state = backlog(factory)
            elapsed = time.monotonic() - drain_started
            lanes = observe_convergence(state, completed_at, elapsed)
            if all(lanes.values()) and elapsed >= min(5, drain_limit):
                converged = True
                break
            if elapsed >= drain_limit:
                break
            if any(not process.is_alive() for process in processes.values()):
                raise RuntimeError("A production worker exited during convergence")
            for kind, row in process_samples(processes).items():
                drain_peaks[kind] = max(drain_peaks[kind], row["rss_bytes"])
            time.sleep(min(1, max(0, drain_limit - (time.monotonic() - drain_started))))
        output["convergence"] = {
            **convergence_outcome(state, completed_at, limits),
            "limit_seconds": drain_limit,
            "final_backlog": state,
        }
        output["convergence"]["passed"] &= converged
        output["phases"].append(
            {
                "name": "drain",
                "seconds": max(time.monotonic() - drain_started, 0.001),
                "cost": statement_delta(drain_before, statement_snapshot(factory)),
                "database_cost": database_delta(drain_db, database_snapshot(factory)),
                "processes": process_delta(drain_cpu, process_samples(processes), drain_peaks),
                "backlog": state,
                "writer_ms": [],
                "ingested": 0,
                "readers": {},
            }
        )
        output["gates"] = gates(
            output["phases"],
            reader_p95_ms=args.reader_p95_ms,
            writer_p95_ms=args.writer_p95_ms,
            max_sql_seconds_per_minute=args.sql_seconds_per_minute,
            max_buffers_per_minute=args.buffers_per_minute,
            max_wal_bytes_per_minute=args.wal_bytes_per_minute,
            max_event_age_seconds=args.max_event_age_seconds,
            max_cpu_seconds_per_minute=args.cpu_seconds_per_minute,
            max_rss_bytes=args.rss_bytes,
            max_global_wal_bytes_per_minute=args.global_wal_bytes_per_minute,
            max_maintenance_age_seconds=args.maintenance_convergence_seconds,
        )
        if not output["convergence"]["passed"]:
            output["gates"]["passed"] = False
            output["gates"]["failures"].append(
                "Captured foreground or maintenance work did not converge within its drain budget"
            )
        if (
            args.scale >= 1
            and args.active_seconds >= 1800
            and args.idle_grace_seconds >= 660
            and args.recovery_seconds >= 120
            and args.isolated_database_cluster
            and int(output["postgresql"]["version_num"]) // 10000 == 17
            and any(
                phase["name"] == "idle-expired" and phase["seconds"] >= 600
                for phase in output["phases"]
            )
            and all(
                phase["ingested"] == phase["planned_ingested"]
                and all(row["samples"] >= 20 for row in phase["readers"].values())
                for phase in output["phases"]
                if phase["name"] in {"active", "recovery"}
            )
            and platform.machine() in {"aarch64", "arm64"}
        ):
            output["qualification"] = "native-scale"
        output["complete"] = True
        return int(not output["gates"]["passed"])
    finally:
        errors = []
        for process in processes.values():
            try:
                stop_worker(process)
            except RuntimeError as error:
                errors.append(str(error))
        engine.dispose()
        output["shutdown_errors"] = errors
        if errors:
            output["complete"] = False
        write_result(args.output, output)
        if errors:
            raise RuntimeError("; ".join(errors))


if __name__ == "__main__":
    raise SystemExit(main())
