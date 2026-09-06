"""Typer application and installed entrypoint; parsing/help never opens services."""

from typing import Annotated, Literal

import typer
from devfeed_core.version import __version__

from devfeed_cli import (
    article_commands,
    commands,
    image_commands,
    operation_commands,
    source_commands,
    status,
    taxonomy_commands,
    topic_commands,
)
from devfeed_cli.runtime import Invocation, invoke

app = typer.Typer(
    name="devfeed",
    help="Submit RSS feeds, configure taxonomy and operate the RQ ingestion pipeline.",
    epilog="Set DEVFEED_DATABASE_URL and DEVFEED_REDIS_URL in the environment or .env. "
    "No API server or account is required. Record commands output JSON.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(source_commands.app, name="sources")
app.add_typer(article_commands.app, name="articles")
app.add_typer(image_commands.app, name="images")
app.add_typer(operation_commands.jobs, name="jobs")
app.add_typer(topic_commands.app, name="topics")
app.add_typer(taxonomy_commands.categories, name="categories")
app.add_typer(taxonomy_commands.tags, name="tags")
app.add_typer(operation_commands.cache, name="cache")
app.add_typer(operation_commands.db, name="db")


def version_option(value: bool) -> None:
    if value:
        typer.echo(f"devfeed {__version__}")
        raise typer.Exit()


@app.callback()
def cli(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=version_option,
            is_eager=True,
            help="Show the app version and exit.",
        ),
    ] = False,
):
    # Do not initialize configuration here: help and shell completion are offline.
    pass


@app.command()
def worker(
    ctx: typer.Context,
    burst: Annotated[bool, typer.Option("--burst", help="Exit when the queue is empty.")] = False,
    name: Annotated[str | None, typer.Option(help="Optional unique worker name.")] = None,
    max_jobs: Annotated[int | None, typer.Option(min=1, max=2_147_483_647)] = None,
    queue: Literal["all", "ingestion", "analysis", "notifications"] = "all",
):
    """Run a common RQ worker; by default consume all enabled queues fairly."""
    invoke(ctx, commands.run_worker, locals())


@app.command()
def scheduler(
    ctx: typer.Context,
    once: Annotated[bool, typer.Option("--once", help="Run one tick and exit.")] = False,
):
    """Run the polling scheduler and durable job dispatcher."""
    invoke(ctx, commands.run_scheduler, locals())


@app.command("status")
def service_status(ctx: typer.Context):
    """Check dependencies, counts, queues and worker/scheduler heartbeats."""
    invoke(ctx, lambda _: status.snapshot(), locals())


def run(argv: list[str] | None = None) -> int:
    """Preserve the programmatic runner's status codes and help/usage exits."""
    invocation = Invocation()
    try:
        app(args=argv, prog_name="devfeed", obj=invocation)
    except SystemExit:
        if invocation.code is not None:
            return invocation.code
        raise
    return 0


def main() -> None:
    raise SystemExit(run())
