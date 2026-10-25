"""Dedicated pipeline/report publisher using an independently budgeted connection."""

import argparse
import logging
import signal
import threading
import time

from devfeed_core.analysis import reuse_candidates
from devfeed_core.catalog_cache import reuse_snapshots
from devfeed_core.config import get_settings
from devfeed_core.db import create_database_engine
from devfeed_core.logging import configure_logging
from devfeed_core.models import (
    ReadModelEvent,
    ReadModelState,
    ReportingDirty,
    ReportRefresh,
    utcnow,
)
from devfeed_core.runtime_files import runtime_marker, write_runtime_marker
from devfeed_core.telemetry import background_cycle, current, start_runtime, stop_runtime
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from devfeed_admin_api.pipeline_projection import project_batch

logger = logging.getLogger(__name__)
BACKLOG_SAMPLE_SECONDS = 30
HEARTBEAT_WAIT_SECONDS = 30
MAX_DRAIN_BATCHES = 10000


def heartbeat_marker(kind):
    if kind == "pipeline":
        return runtime_marker("pipeline-heartbeat")
    if kind == "reports":
        return runtime_marker("reports-heartbeat")
    raise ValueError("Unknown read-model publisher")


def record_backlog(factory, kind):
    runtime = current()
    if runtime is None:
        return
    model, created = (
        (ReadModelEvent, ReadModelEvent.created_at)
        if kind == "pipeline"
        else (
            ReportRefresh,
            ReportRefresh.requested_at,
        )
    )
    with factory() as session:
        count, oldest = session.execute(
            select(func.count(), func.min(created)).select_from(model)
        ).one()
        measurements = [(kind, count, oldest)]
        if kind == "reports":
            count, oldest = session.execute(
                select(func.count(), func.min(ReportingDirty.created_at)).select_from(
                    ReportingDirty
                )
            ).one()
            measurements.append(("reporting-facts", count, oldest))
    for queue_kind, count, oldest in measurements:
        attributes = {"kind": queue_kind}
        runtime.instruments["read_model_pending"].set(count, attributes)
        runtime.instruments["read_model_oldest"].set(
            max(0.0, (utcnow() - oldest).total_seconds()) if oldest else 0,
            attributes,
        )


def duty_delay(elapsed, duty_cycle, poll_seconds):
    """Pay for each bounded batch before starting the next one.

    This caps sustained worker wall-time occupancy, including failed work. It is
    deliberately not a claim about PostgreSQL CPU utilization or query parallelism.
    """
    return max(poll_seconds, elapsed * (1 / duty_cycle - 1))


def wait_for_budget(stopping, seconds, marker, *, healthy):
    """Keep a healthy worker observable and shutdown responsive while it yields."""
    while seconds > 0 and not stopping.is_set():
        interval = min(seconds, HEARTBEAT_WAIT_SECONDS)
        stopping.wait(interval)
        seconds -= interval
        if healthy and not stopping.is_set():
            write_runtime_marker(marker, str(time.time()))


def pipeline_drained(factory):
    with factory() as session:
        state = session.get(ReadModelState, "pipeline")
        pending = session.scalar(select(ReadModelEvent.id).limit(1))
        if pending is not None or not state or not state.payload.get("backfill_complete"):
            raise RuntimeError(
                "Pipeline backfill is busy or its publisher lease expired; retry the drain"
            )
    return True


def reports_drained(factory):
    from devfeed_core.reporting_facts import ReportingFactsUnavailable, require_reporting_facts

    with factory() as session:
        pending = session.scalar(
            select(ReportRefresh).order_by(ReportRefresh.available_at).limit(1)
        )
        if pending is not None:
            if pending.lease_until is not None and pending.lease_until > utcnow():
                raise RuntimeError("Report publication is busy; retry the drain")
            if pending.last_error not in {None, "ProjectionNotReady"}:
                raise RuntimeError(
                    f"Report publication failed ({pending.group}/{pending.days}: "
                    f"{pending.last_error}); retry after its cooldown"
                )
        try:
            require_reporting_facts(session)
        except ReportingFactsUnavailable:
            return False
        return pending is None


@reuse_snapshots()
@reuse_candidates()
def run(*, once=False, drain=False, kind="pipeline"):
    from devfeed_admin_api.overview_projection import refresh_overview_projections

    marker = heartbeat_marker(kind)
    settings = get_settings()
    configure_logging("read-model-worker", settings.log_level, settings.log_format)
    telemetry = start_runtime("read-model-worker")
    # NullPool releases each physical session after its short transaction. Use a
    # dedicated PgBouncer login/pool in production to reserve interactive slots.
    engine = create_database_engine(
        settings.model_copy(
            update={
                "database_url": settings.read_model_database_url or settings.database_url,
                "database_pool_enabled": False,
                "database_application_name": f"devfeed-read-model-{kind}",
            }
        )
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    stopping = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stopping.set())
    last_backlog_sample = None
    duty_cycle = (
        settings.read_model_pipeline_duty_cycle
        if kind == "pipeline"
        else settings.read_model_report_duty_cycle
    )
    pipeline_batch = min(settings.read_model_batch_size, 200)
    batches = 0
    try:
        while not stopping.is_set():
            started = time.monotonic()
            healthy = False
            finished = False
            try:
                processed = 0
                reports = 0
                with background_cycle(f"read-models.{kind}"):
                    if kind == "pipeline":
                        processed = project_batch(factory, batch=pipeline_batch)
                    else:
                        options = {"seed": not batches, "fail_fast": True} if drain else {}
                        reports = refresh_overview_projections(
                            factory, limit=1, now=utcnow(), **options
                        )
                if drain and kind == "pipeline" and not processed:
                    finished = pipeline_drained(factory)
                elif drain and kind == "reports":
                    finished = reports_drained(factory)
                # Exact queue counts grow with backlog size. Sample telemetry
                # independently of the bounded publisher and its health heartbeat.
                sampled_at = time.monotonic()
                if (
                    last_backlog_sample is None
                    or sampled_at - last_backlog_sample >= BACKLOG_SAMPLE_SECONDS
                ):
                    record_backlog(factory, kind)
                    last_backlog_sample = sampled_at
                write_runtime_marker(marker, str(time.time()))
                healthy = True
                logger.info(
                    "read_model_cycle_completed",
                    extra={
                        "events": processed,
                        "reports": reports,
                        "batch_size": pipeline_batch if kind == "pipeline" else 1,
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    },
                )
            except Exception:
                logger.exception("read_model_cycle_failed")
                if once or drain:
                    raise
            elapsed = time.monotonic() - started
            if kind == "pipeline":
                if elapsed > 30:
                    pipeline_batch = max(1, pipeline_batch // 2)
                elif healthy and processed and elapsed < 10:
                    pipeline_batch = min(
                        settings.read_model_batch_size,
                        pipeline_batch + max(1, pipeline_batch // 10),
                    )
            if once or finished:
                return
            if drain:
                batches += 1
                if batches >= MAX_DRAIN_BATCHES:
                    raise RuntimeError(
                        f"{kind.capitalize()} projection has not drained within its bounded run"
                    )
            wait_for_budget(
                stopping,
                duty_delay(elapsed, duty_cycle, settings.read_model_poll_seconds),
                marker,
                healthy=healthy,
            )
    finally:
        engine.dispose()
        stop_runtime(telemetry)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--kind", choices=("pipeline", "reports"), default="pipeline")
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument(
        "--drain", action="store_true", help="Finish bounded backfill and requested publications"
    )
    args = parser.parse_args()
    if args.healthcheck:
        marker = heartbeat_marker(args.kind)
        try:
            age = time.time() - marker.stat().st_mtime
        except FileNotFoundError:
            raise SystemExit(1) from None
        if age > 180:
            raise SystemExit(1)
        return
    run(once=args.once, drain=args.drain, kind=args.kind)


if __name__ == "__main__":
    main()
