"""Search operations are explicit and stay offline during CLI help parsing."""

import json
import logging
import signal
import threading
import time
from pathlib import Path

import typer

app = typer.Typer(help="Backfill and synchronize the Typesense search index.")


@app.command()
def backfill():
    """Queue all existing records for indexing; normal traffic uses change events."""
    from devfeed_core.db import session_factory
    from devfeed_core.search_index import backfill as enqueue

    typer.echo(json.dumps({"queued": enqueue(session_factory())}))


@app.command()
def setup():
    """Create collections and the configured search-only key (never print keys)."""
    from devfeed_core.config import get_settings
    from devfeed_core.search_engine import KINDS, Typesense

    engine = Typesense(admin=True)
    engine.setup()
    settings = get_settings()
    if settings.search_query_key:
        engine.request(
            "POST",
            "/keys",
            data={
                "description": "DevFeed reader search",
                "actions": ["documents:search"],
                "collections": [engine.collection(kind) for kind in KINDS],
                "value": settings.search_query_key.get_secret_value(),
            },
            allowed=(409,),
        )
    typer.echo(json.dumps({"status": "ready"}))


@app.command()
def worker(once: bool = False):
    """Run the lightweight index consumer; preserve pending events on failure."""
    from devfeed_core.config import get_settings
    from devfeed_core.logging import configure_logging
    from devfeed_core.search_engine import Typesense
    from devfeed_core.telemetry import start_runtime, stop_runtime

    settings = get_settings()
    configure_logging("search-indexer", settings.log_level, settings.log_format)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    engine = Typesense(admin=True)
    engine.setup()
    telemetry = start_runtime("search-indexer") if not once else None
    try:
        _consume_index(stop, engine, once)
    finally:
        stop_runtime(telemetry)


def _consume_index(stop, engine, once):
    from devfeed_core.config import get_settings
    from devfeed_core.db import session_factory
    from devfeed_core.search_index import reconcile, sync_batch
    from devfeed_core.telemetry import background_cycle, current

    settings = get_settings()
    last_reconcile = 0.0
    last_heartbeat = None
    while not stop.is_set():
        if last_heartbeat and (runtime := current()):
            runtime.metrics.search_heartbeat_age.labels(runtime.service).set(
                max(0.0, time.time() - last_heartbeat)
            )
        if current() and time.monotonic() >= last_reconcile:
            try:
                with background_cycle("search.reconcile"):
                    reconcile(session_factory(), engine)
            except Exception as exc:
                logging.getLogger(__name__).error(
                    "search_index_reconciliation_failed",
                    extra={"error_type": type(exc).__name__},
                )
            finally:
                last_reconcile = time.monotonic() + settings.search_reconcile_interval_seconds
        try:
            with background_cycle("search.sync"):
                count = sync_batch(session_factory(), engine)
        except Exception as exc:
            logging.getLogger(__name__).error(
                "search_index_sync_failed", extra={"error_type": type(exc).__name__}
            )
            if once:
                raise typer.Exit(1) from None
            stop.wait(5)
        else:
            last_heartbeat = time.time()
            Path("/tmp/devfeed-search-heartbeat").write_text(str(last_heartbeat))
            if runtime := current():
                runtime.metrics.search_heartbeat_age.labels(runtime.service).set(0)
            if once:
                typer.echo(json.dumps({"processed": count}))
                return
            if not count:
                stop.wait(1)
