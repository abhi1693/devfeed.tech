from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from devfeed_cli import commands
from devfeed_cli.options import Identifier, JobStatus, Limit, Offset, group
from devfeed_cli.runtime import invoke

jobs = group("Inspect ingestion runs and retry failures.")
cache = group("Manage disposable GET response caching (never RQ data).")
db = group("Apply or inspect schema migrations.")


@jobs.command("list")
def list_jobs(
    ctx: typer.Context,
    limit: Limit = 100,
    offset: Offset = 0,
    source_id: UUID | None = None,
    status: JobStatus | None = None,
):
    """List ingestion runs."""
    invoke(ctx, commands.job_list, locals())


@jobs.command()
def show(ctx: typer.Context, id: Identifier):
    """Inspect a run's status, counters and error."""
    invoke(ctx, commands.job_show, locals())


@jobs.command()
def retry(ctx: typer.Context, id: Identifier):
    """Request a new run for a failed job, preserving its history."""
    invoke(ctx, commands.job_retry, locals())


@jobs.command()
def dispatch(ctx: typer.Context, id: Identifier):
    """Dispatch a queued job to RQ now, bypassing queued delays."""
    invoke(ctx, commands.job_dispatch, locals())


@cache.command("clear")
def cache_clear(ctx: typer.Context):
    """Invalidate this database's API responses without flushing Redis."""
    invoke(ctx, commands.cache_clear, locals())


@db.command("upgrade", help="Apply pending schema migrations.")
@db.command("check", help="Check for model changes that need a migration.")
@db.command("current", help="Display the current schema revision.")
def migrate(
    ctx: typer.Context,
    config: Annotated[
        Path | None, typer.Option(help="alembic.ini path; otherwise search parent directories.")
    ] = None,
):
    invoke(ctx, commands.migrate, locals())
