"""Search operations are explicit and stay offline during CLI help parsing."""

import json

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
    """Run the dedicated indexer runtime; retained as a CLI compatibility command."""
    from devfeed_search_indexer.runtime import run_indexer

    if run_indexer(once=once, output=typer.echo):
        raise typer.Exit(1)
