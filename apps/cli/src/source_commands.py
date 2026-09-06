"""Typer commands for trusted source submission, profiles and review."""

from typing import Annotated
from uuid import UUID

import typer
from devfeed_core.source_types import SourceType

from devfeed_cli import commands
from devfeed_cli.options import (
    Force,
    Identifier,
    JobStatus,
    Limit,
    Offset,
    PollInterval,
    ReviewStatus,
    boolean_pair,
    group,
    updates,
)
from devfeed_cli.runtime import invoke

app = group("Submit, inspect and configure RSS/Atom sources.")


@app.command("add")
def add(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="Public RSS/Atom feed URL.")],
    source_type: Annotated[SourceType, typer.Option("--type")],
    name: Annotated[
        str | None, typer.Option(help="Defaults to RSS/Atom title, then hostname.")
    ] = None,
    poll_interval: PollInterval = 1800,
    disabled: Annotated[
        bool, typer.Option("--disabled", help="Do not schedule ingestion.")
    ] = False,
    description: str | None = None,
    website_url: str | None = None,
    logo_url: str | None = None,
    image_url: str | None = None,
    language: str | None = None,
    submitted_by: Annotated[
        str | None, typer.Option(help="Attribution, not verified identity.")
    ] = None,
    submitter_url: str | None = None,
):
    """Validate before saving; trust new sources, preserve existing source review/settings."""
    invoke(ctx, commands.source_add, locals())


@app.command("import")
def import_sources(
    ctx: typer.Context,
    file: Annotated[str, typer.Argument(help="UTF-8 file of feed URLs, or - for stdin.")],
    source_type: Annotated[SourceType, typer.Option("--type")],
    poll_interval: PollInterval = 1800,
):
    """Validate every unique feed before saving the batch; one URL per line."""
    invoke(ctx, commands.source_import, locals())


@app.command("list")
def list_sources(
    ctx: typer.Context,
    limit: Limit = 100,
    offset: Offset = 0,
    source_type: Annotated[SourceType | None, typer.Option("--type")] = None,
    approval_status: Annotated[ReviewStatus | None, typer.Option("--status")] = None,
    enabled: Annotated[bool, typer.Option("--enabled")] = False,
    disabled: Annotated[bool, typer.Option("--disabled")] = False,
):
    """List sources, including disabled ones unless filtered."""
    invoke(
        ctx,
        commands.source_list,
        locals(),
        enabled=boolean_pair(enabled, disabled, "--enabled and --disabled"),
    )


@app.command()
def show(ctx: typer.Context, id: Identifier):
    """Inspect source profile, review and polling state."""
    invoke(ctx, commands.source_show, locals())


@app.command()
def update(
    ctx: typer.Context,
    id: Identifier,
    name: str | None = None,
    description: str | None = None,
    website_url: str | None = None,
    logo_url: str | None = None,
    image_url: str | None = None,
    language: str | None = None,
    clear_description: Annotated[bool, typer.Option("--clear-description")] = False,
    clear_website_url: Annotated[bool, typer.Option("--clear-website-url")] = False,
    clear_logo_url: Annotated[bool, typer.Option("--clear-logo-url")] = False,
    clear_image_url: Annotated[bool, typer.Option("--clear-image-url")] = False,
    clear_language: Annotated[bool, typer.Option("--clear-language")] = False,
    poll_interval: Annotated[
        int | None, typer.Option("--poll-interval", min=300, max=604800)
    ] = None,
    enable: Annotated[bool, typer.Option("--enable")] = False,
    disable: Annotated[bool, typer.Option("--disable")] = False,
):
    """Change only specified source fields; feed URL and source type are immutable."""
    fields = ("name", "description", "website_url", "logo_url", "image_url", "language")
    values = updates(
        ctx,
        locals(),
        {**dict(zip(fields, fields, strict=True)), "poll_interval": "poll_interval_seconds"},
        {f"clear_{field}": (field, None) for field in fields if field != "name"},
    )
    enabled = boolean_pair(enable, disable, "--enable and --disable")
    if enabled is not None:
        values["enabled"] = enabled
    invoke(ctx, commands.source_update, values, id=id)


@app.command()
def fetch(ctx: typer.Context, id: Identifier, force: Force = False):
    """Queue a refresh, coalescing an existing active job."""
    invoke(ctx, commands.source_fetch, locals())


@app.command()
def approve(ctx: typer.Context, id: Identifier, by: str | None = None, note: str | None = None):
    """Record an explicit source approval."""
    invoke(ctx, commands.source_review, locals(), decision="approved")


@app.command()
def reject(
    ctx: typer.Context,
    id: Identifier,
    note: Annotated[str, typer.Option("--reason")],
    by: str | None = None,
):
    """Reject a source with a required reason."""
    invoke(ctx, commands.source_review, locals(), decision="rejected")


@app.command("review-history")
def review_history(ctx: typer.Context, id: Identifier, limit: Limit = 100, offset: Offset = 0):
    """Inspect source approval/rejection history."""
    invoke(ctx, commands.source_review_history, locals())


@app.command()
def enrich(ctx: typer.Context, id: Identifier, force: Force = False):
    """Queue missing source profile metadata, including sources pending review."""
    invoke(ctx, commands.source_enrich, locals())


@app.command("enrichment-jobs")
def enrichment_jobs(
    ctx: typer.Context,
    source_id: UUID | None = None,
    status: JobStatus | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
):
    """Inspect source metadata jobs and failures."""
    invoke(ctx, commands.source_enrichment_jobs, locals())


@app.command("enrichment-dispatch")
def enrichment_dispatch(ctx: typer.Context, id: Identifier):
    """Dispatch a queued source enrichment job ID."""
    invoke(ctx, commands.source_enrichment_dispatch, locals())
