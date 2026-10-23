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
from devfeed_core.models import ReadModelEvent, ReportRefresh, utcnow
from devfeed_core.runtime_files import runtime_marker, write_runtime_marker
from devfeed_core.telemetry import background_cycle, current, start_runtime, stop_runtime
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from devfeed_admin_api.pipeline_projection import drain_pipeline, project_batch

logger = logging.getLogger(__name__)
BACKLOG_SAMPLE_SECONDS = 30


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
    attributes = {"kind": kind}
    runtime.instruments["read_model_pending"].set(count, attributes)
    runtime.instruments["read_model_oldest"].set(
        max(0.0, (utcnow() - oldest).total_seconds()) if oldest else 0,
        attributes,
    )


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
            }
        )
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    stopping = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stopping.set())
    last_backlog_sample = None
    try:
        while not stopping.is_set():
            started = time.monotonic()
            try:
                processed = 0
                reports = 0
                with background_cycle(f"read-models.{kind}"):
                    if kind == "pipeline":
                        processed = (
                            drain_pipeline(factory, batch=settings.read_model_batch_size)
                            if drain
                            else project_batch(factory, batch=settings.read_model_batch_size)
                        )
                    else:
                        reports = refresh_overview_projections(factory, limit=1, now=utcnow())
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
                logger.info(
                    "read_model_cycle_completed",
                    extra={
                        "events": processed,
                        "reports": reports,
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    },
                )
            except Exception:
                logger.exception("read_model_cycle_failed")
                if once or drain:
                    raise
            if once or drain:
                return
            stopping.wait(settings.read_model_poll_seconds)
    finally:
        engine.dispose()
        stop_runtime(telemetry)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--kind", choices=("pipeline", "reports"), default="pipeline")
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument(
        "--drain", action="store_true", help="Finish initial pipeline backfill and exit"
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
