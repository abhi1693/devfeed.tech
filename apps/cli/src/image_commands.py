from uuid import UUID

import typer

from devfeed_cli import images
from devfeed_cli.options import Force, Identifier, JobStatus, Limit, Offset, group
from devfeed_cli.runtime import invoke

app = group("Discover missing article image URLs using RQ.")


@app.command("fetch", help="Queue image discovery for an article ID; keep existing images.")
@app.command("retry", help="Retry a failed image job ID, preserving its history.")
def fetch_or_retry(ctx: typer.Context, id: Identifier, force: Force = False):
    invoke(ctx, images.fetch if ctx.info_name == "fetch" else images.retry, locals())


@app.command()
def show(ctx: typer.Context, id: Identifier):
    """Inspect an image job ID."""
    invoke(ctx, images.show, locals())


@app.command()
def dispatch(ctx: typer.Context, id: Identifier):
    """Publish a queued image job ID to RQ immediately."""
    invoke(ctx, images.dispatch, locals())


@app.command()
def backfill(ctx: typer.Context, limit: Limit = 100):
    """Queue a bounded batch of missing images that have never been checked."""
    invoke(ctx, images.backfill, locals())


@app.command()
def jobs(
    ctx: typer.Context,
    limit: Limit = 100,
    offset: Offset = 0,
    article_id: UUID | None = None,
    status: JobStatus | None = None,
):
    """List image lookup jobs."""
    invoke(ctx, images.jobs, locals())
