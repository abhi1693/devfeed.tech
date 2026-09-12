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
    from devfeed_core.db import session_factory
    from devfeed_core.logging import configure_logging
    from devfeed_core.search_engine import Typesense
    from devfeed_core.search_index import sync_batch

    settings = get_settings()
    configure_logging("search-indexer", settings.log_level, settings.log_format)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    engine = Typesense(admin=True)
    engine.setup()
    while not stop.is_set():
        try:
            count = sync_batch(session_factory(), engine)
        except Exception as exc:
            logging.getLogger(__name__).error(
                "search_index_sync_failed", extra={"error_type": type(exc).__name__}
            )
            if once:
                raise typer.Exit(1) from None
            stop.wait(5)
        else:
            Path("/tmp/devfeed-search-heartbeat").write_text(str(time.time()))
            if once:
                typer.echo(json.dumps({"processed": count}))
                return
            if not count:
                stop.wait(1)
