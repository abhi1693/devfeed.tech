"""Cached, read-only product and durable-queue telemetry.

Scrapes never query dependencies. Rolling-window counts are gauges (not counters)
and remain correct when jobs are deleted or workers restart. RQ return values are
not used to infer durable success. All SQL selects metadata/aggregates only.
"""

import logging
import signal
import threading
import time
from collections import Counter
from datetime import UTC, datetime, timedelta

from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import and_, case, event, func, select, text

from devfeed_core.ai_capacity import cooldown_remaining
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.job_definitions import JOB_DEFINITIONS, queue_lanes
from devfeed_core.logging import configure_logging
from devfeed_core.models import Article, SearchEvent, Source, Topic
from devfeed_core.redis import create_redis
from devfeed_core.telemetry import start_runtime, stop_runtime
from devfeed_core.worker_queues import QUEUES

logger = logging.getLogger(__name__)
STATUSES = ("queued", "running", "succeeded", "failed")
WINDOWS = {"5m": 300, "1h": 3600, "24h": 86400}
ERRORS = (
    "invalid_analysis_result",
    "citation_temporarily_unavailable",
    "codex_unavailable",
    "provider_capacity_exhausted",
    "topic_verification_uncertain",
    "relationship_verification_uncertain",
    "timeout",
    "other",
)


def gauge(name, help_text, labels, values):
    metric = GaugeMetricFamily("devfeed_" + name, help_text, labels=labels)
    for keys, value in values:
        metric.add_metric(list(keys), float(value or 0))
    return metric


def job_kind(definition):
    return "article-analysis" if definition.kind == "analysis" else definition.kind


def lane_statement(definition, condition, now):
    model = definition.model
    statement = (
        select(
            model.status,
            func.count().label("count"),
            func.count()
            .filter(and_(model.status == "queued", model.attempts > 0))
            .label("retrying"),
            func.count()
            .filter(and_(model.status == "queued", model.available_at <= now))
            .label("due"),
            func.min(model.available_at)
            .filter(and_(model.status == "queued", model.available_at <= now))
            .label("oldest_due"),
            func.count()
            .filter(and_(model.status == "running", model.lease_until < now))
            .label("expired"),
        )
        .where(model.status.in_(("queued", "running")))
        .group_by(model.status)
    )
    return statement.where(condition) if condition is not None else statement


def completion_statement(definition, since, until=None):
    model = definition.model
    latency = func.extract("epoch", model.finished_at - model.created_at)
    # FILTER excludes malformed historical records instead of presenting negative latency.
    return (
        select(
            model.status,
            func.count().label("count"),
            func.count().filter(model.attempts > 1).label("retried"),
            func.sum(func.greatest(model.attempts - 1, 0)).label("retries"),
            func.percentile_cont(0.5).within_group(latency).filter(latency >= 0).label("p50"),
            func.percentile_cont(0.95).within_group(latency).filter(latency >= 0).label("p95"),
        )
        .where(
            model.finished_at >= since,
            model.finished_at <= (until or datetime.now(UTC)),
            model.status.in_(("succeeded", "failed")),
        )
        .group_by(model.status)
    )


def database_snapshot(engine, now, timeout_ms):
    states, retrying, due, ages, expired = [], [], [], [], []
    completed, retries, retried, latencies, latest = [], [], [], [], []
    errors: list = []
    retained: list = []
    deadline = time.monotonic() + 10

    def enforce_budget(*_):
        if time.monotonic() > deadline:
            raise TimeoutError("Exporter refresh budget exceeded")

    with engine.connect() as connection, connection.begin():
        event.listen(connection, "before_cursor_execute", enforce_budget)
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        connection.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": str(timeout_ms)},
        )
        for definition, queue, condition in queue_lanes():
            rows = connection.execute(lane_statement(definition, condition, now)).mappings().all()
            by_status = {row["status"]: row for row in rows}
            for status in ("queued", "running"):
                states.append(
                    (
                        (queue, job_kind(definition), status),
                        by_status.get(status, {}).get("count", 0),
                    )
                )
            queued = by_status.get("queued", {})
            retrying.append(((queue, job_kind(definition)), queued.get("retrying", 0)))
            due.append(((queue, job_kind(definition)), queued.get("due", 0)))
            oldest = queued.get("oldest_due")
            ages.append(
                (
                    (queue, job_kind(definition)),
                    max(0, (now - oldest).total_seconds()) if oldest else 0,
                )
            )
            expired.append(
                ((queue, job_kind(definition)), by_status.get("running", {}).get("expired", 0))
            )
        for definition in JOB_DEFINITIONS.values():
            model, kind = definition.model, job_kind(definition)
            totals = dict(
                connection.execute(select(model.status, func.count()).group_by(model.status)).all()
            )
            retained.extend(((kind, status), totals.get(status, 0)) for status in STATUSES)
            last = connection.scalar(
                select(func.max(model.finished_at)).where(model.finished_at <= now)
            )
            latest.append(((kind,), last.timestamp() if last else 0))
            for window, seconds in WINDOWS.items():
                rows = (
                    connection.execute(
                        completion_statement(definition, now - timedelta(seconds=seconds), now)
                    )
                    .mappings()
                    .all()
                )
                by_status = {row["status"]: row for row in rows}
                for status in ("succeeded", "failed"):
                    row = by_status.get(status, {})
                    completed.append(((kind, status, window), row.get("count", 0)))
                    retried.append(((kind, status, window), row.get("retried", 0)))
                    retries.append(((kind, status, window), row.get("retries", 0)))
                    for quantile, field in (("0.5", "p50"), ("0.95", "p95")):
                        # No samples means no quantile, never a fabricated zero latency.
                        if row.get(field) is not None:
                            latencies.append(((kind, status, window, quantile), row[field]))
            error_family = case(
                (model.error.in_(ERRORS[:-2]), model.error),
                (func.lower(model.error).like("%timeout%"), "timeout"),
                else_="other",
            )
            counts = dict(
                connection.execute(
                    select(error_family, func.count())
                    .where(
                        model.status == "failed",
                        model.finished_at >= now - timedelta(hours=24),
                        model.finished_at <= now,
                    )
                    .group_by(error_family)
                ).all()
            )
            errors.extend(((kind, error), counts.get(error, 0)) for error in ERRORS)
        product: list = []
        for _product_model, label, state_column in (
            (Article, "articles", Article.publication_status),
            (Source, "sources", Source.approval_status),
            (Topic, "topics", Topic.status),
        ):
            product.extend(
                ((label, status), count)
                for status, count in connection.execute(
                    select(state_column, func.count()).group_by(state_column)
                )
            )
        fresh = connection.scalar(select(func.max(Article.discovered_at)))
        source_errors = connection.scalar(
            select(func.count()).select_from(Source).where(Source.consecutive_failures > 0)
        )
        search_count, search_oldest = connection.execute(
            select(func.count(), func.min(SearchEvent.created_at))
        ).one()
    return [
        gauge(
            "jobs", "Live durable jobs by current routing lane", ["queue", "kind", "status"], states
        ),
        gauge(
            "jobs_retrying",
            "Queued jobs with at least one prior attempt",
            ["queue", "kind"],
            retrying,
        ),
        gauge("jobs_due", "Durable queued jobs eligible to dispatch now", ["queue", "kind"], due),
        gauge(
            "jobs_oldest_due_age_seconds",
            "Age since oldest queued job became eligible",
            ["queue", "kind"],
            ages,
        ),
        gauge(
            "jobs_expired_leases",
            "Running durable jobs with expired leases",
            ["queue", "kind"],
            expired,
        ),
        gauge(
            "jobs_retained",
            "Retained durable rows; decreases on deletion, not a counter",
            ["kind", "status"],
            retained,
        ),
        gauge(
            "jobs_completed_window",
            "Durable completions in a rolling window; do not use rate()",
            ["kind", "status", "window"],
            completed,
        ),
        gauge(
            "jobs_retried_window",
            "Completed jobs needing multiple attempts in a rolling window",
            ["kind", "status", "window"],
            retried,
        ),
        gauge(
            "job_retry_attempts_window",
            "Extra attempts of jobs completed in a rolling window",
            ["kind", "status", "window"],
            retries,
        ),
        gauge(
            "job_completion_latency_seconds",
            "End-to-end completed-job latency including queue and retries",
            ["kind", "status", "window", "quantile"],
            latencies,
        ),
        gauge(
            "job_last_completion_timestamp_seconds",
            "Most recent durable completion",
            ["kind"],
            latest,
        ),
        gauge(
            "job_failures_24h",
            "Terminal failures in the last 24 hours with bounded error families",
            ["kind", "error"],
            errors,
        ),
        gauge(
            "product_records",
            "Product inventory by publication or approval state",
            ["entity", "status"],
            product,
        ),
        gauge(
            "product_latest_article_timestamp_seconds",
            "Last article received, independent of publication",
            [],
            [((), fresh.timestamp() if fresh else 0)],
        ),
        gauge(
            "sources_with_fetch_errors",
            "Sources with consecutive feed failures",
            [],
            [((), source_errors)],
        ),
        gauge(
            "search_pending_events", "Durable search projection backlog", [], [((), search_count)]
        ),
        gauge(
            "search_oldest_event_age_seconds",
            "Age of oldest unindexed event",
            [],
            [((), max(0, (now - search_oldest).total_seconds()) if search_oldest else 0)],
        ),
    ]


def redis_snapshot(redis, now):
    pipeline = redis.pipeline(transaction=False)
    for queue in QUEUES:
        pipeline.llen("rq:queue:" + queue)
    depths = list(zip(QUEUES, pipeline.execute(), strict=True))
    keys = list(redis.smembers("rq:workers"))
    if len(keys) > 1000:
        raise ValueError("Worker registry exceeds exporter safety bound")
    pipeline = redis.pipeline(transaction=False)
    for key in keys:
        pipeline.hmget(key, "queues", "state", "last_heartbeat", "death")
        pipeline.ttl(key)
    records = pipeline.execute()
    workers: Counter = Counter()
    stale: Counter = Counter()
    for index in range(0, len(records), 2):
        (queues, state, heartbeat, death), ttl = records[index : index + 2]
        if not queues or death or ttl <= 0:
            continue
        try:
            last = datetime.fromisoformat(heartbeat.decode()).replace(tzinfo=UTC)
        except (ValueError, AttributeError):
            continue
        state = state.decode() if state else "unknown"
        state = state if state in {"idle", "busy", "suspended", "started"} else "unknown"
        for queue in queues.decode().split(","):
            if queue not in QUEUES:
                continue
            if (now - last).total_seconds() > 90:
                stale[queue] += 1
            else:
                workers[queue, state] += 1
    return [
        gauge(
            "queue_deliveries",
            "Waiting Redis deliveries, distinct from durable due jobs",
            ["queue"],
            [((queue,), count) for queue, count in depths],
        ),
        gauge(
            "queue_workers",
            "Live recent-heartbeat workers registered to each queue",
            ["queue", "state"],
            [
                ((queue, state), workers[queue, state])
                for queue in QUEUES
                for state in ("idle", "busy", "suspended", "started", "unknown")
            ],
        ),
        gauge(
            "queue_stale_workers",
            "Unexpired registrations with heartbeat older than 90 seconds",
            ["queue"],
            [((queue,), stale[queue]) for queue in QUEUES],
        ),
        gauge(
            "ai_cooldown_seconds",
            "Remaining shared AI provider capacity cooldown",
            [],
            [((), cooldown_remaining(redis))],
        ),
    ]


class SnapshotCollector:
    def __init__(self):
        self.snapshots: dict[str, list] = {}
        self.last_success: dict[str, float] = {}
        self.healthy: dict[str, bool] = {}
        self.errors: Counter = Counter()
        self.durations: dict[str, float] = {}

    def describe(self):
        return []  # Registration must never contact dependencies.

    def collect(self):
        for snapshot in list(self.snapshots.values()):
            yield from snapshot
        names = ("database", "redis", "codex")
        yield gauge(
            "exporter_dependency_up",
            "Whether the latest refresh succeeded",
            ["dependency"],
            [((name,), self.healthy.get(name, False)) for name in names],
        )
        yield gauge(
            "exporter_last_success_timestamp_seconds",
            "Timestamp of the last good cached snapshot",
            ["dependency"],
            [((name,), self.last_success.get(name, 0)) for name in names],
        )
        yield gauge(
            "exporter_refresh_duration_seconds",
            "Duration of the last dependency refresh",
            ["dependency"],
            [((name,), self.durations.get(name, 0)) for name in names],
        )
        # Gauge naming is deliberate: this exporter state resets on process restart.
        yield gauge(
            "exporter_refresh_errors",
            "Refresh errors since this exporter started",
            ["dependency"],
            [((name,), self.errors[name]) for name in names],
        )

    def refresh(self, name, callback):
        started = time.monotonic()
        try:
            snapshot = callback()
        except Exception as exc:
            self.healthy[name] = False
            self.errors[name] += 1
            logger.warning(
                "exporter_refresh_failed",
                extra={"dependency": name, "error_type": type(exc).__name__},
            )
        else:
            self.snapshots[name] = snapshot
            self.healthy[name] = True
            self.last_success[name] = time.time()
        finally:
            self.durations[name] = time.monotonic() - started


def codex_snapshot():
    from devfeed_aggregator.codex_client import CodexClient

    reason = CodexClient(get_settings()).readiness()
    return [
        gauge(
            "ai_ready",
            "Authenticated Codex account readiness (no inference)",
            [],
            [((), reason is None)],
        )
    ]


def run():
    settings = get_settings()
    if not settings.metrics_enabled:
        raise ValueError("The exporter requires DEVFEED_METRICS_ENABLED")
    configure_logging("exporter", settings.log_level, settings.log_format)
    runtime = start_runtime("exporter")
    assert runtime is not None
    collector = SnapshotCollector()
    runtime.metrics.registry.register(collector)
    redis = create_redis(settings)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    try:
        while not stop.is_set():
            now = datetime.now(UTC)
            collector.refresh(
                "database",
                lambda now=now: database_snapshot(
                    get_engine(), now, settings.exporter_query_timeout_ms
                ),
            )
            collector.refresh("redis", lambda now=now: redis_snapshot(redis, now))
            collector.refresh("codex", codex_snapshot)
            stop.wait(settings.exporter_refresh_seconds)
    finally:
        redis.close()
        stop_runtime(runtime)


if __name__ == "__main__":
    run()
