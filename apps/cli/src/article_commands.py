"""Typed article enrichment, classification and editorial commands."""

from typing import Annotated, Literal
from uuid import UUID

import typer

from devfeed_cli import articles, editorial
from devfeed_cli.options import Force, Identifier, JobStatus, Limit, Offset, ReviewStatus, group
from devfeed_cli.runtime import invoke

app = group("Maintain article metadata, classification and publication.")
Revision = Annotated[int | None, typer.Option(min=0, max=2_147_483_647)]
DryRun = Annotated[bool, typer.Option("--dry-run", help="Preview without writing changes.")]


@app.command("detect-languages")
def detect_languages(
    ctx: typer.Context,
    limit: Limit = 100,
    after: UUID | None = None,
    source_id: UUID | None = None,
    dry_run: DryRun = False,
):
    """Detect languages offline; correct old feed hints in a bounded batch."""
    invoke(ctx, articles.detect_languages, locals())


@app.command("enrich", help="Queue original-page lookup for an article ID.")
@app.command("retry", help="Retry a failed article enrichment job ID.")
def enrich_or_retry(ctx: typer.Context, id: Identifier, force: Force = False):
    invoke(ctx, articles.enrich if ctx.info_name == "enrich" else articles.retry, locals())


@app.command()
def show(ctx: typer.Context, id: Identifier):
    """Inspect an article enrichment job ID (use inspect for the article itself)."""
    invoke(ctx, articles.show, locals())


@app.command()
def dispatch(ctx: typer.Context, id: Identifier):
    """Send a queued article enrichment job ID to RQ immediately."""
    invoke(ctx, articles.dispatch, locals())


@app.command()
def backfill(
    ctx: typer.Context,
    limit: Limit = 100,
    source_id: UUID | None = None,
    dispatch: Annotated[
        bool, typer.Option("--dispatch", help="Publish saved jobs immediately.")
    ] = False,
):
    """Queue original-page enrichment for never-checked articles."""
    invoke(ctx, articles.backfill, locals())


@app.command()
def jobs(
    ctx: typer.Context,
    limit: Limit = 100,
    offset: Offset = 0,
    article_id: UUID | None = None,
    status: JobStatus | None = None,
):
    """List article enrichment outcomes and failures."""
    invoke(ctx, articles.jobs, locals())


@app.command("approve", help="Approve an article explicitly.")
@app.command("publish", help="Publish an approved article that meets all requirements.")
@app.command("unpublish", help="Remove an article from the public feed.")
def decide(
    ctx: typer.Context,
    id: Identifier,
    by: str | None = None,
    note: str | None = None,
    revision: Revision = None,
    dry_run: DryRun = False,
):
    invoke(ctx, editorial.decide, locals())


@app.command()
def reject(
    ctx: typer.Context,
    id: Identifier,
    note: Annotated[str, typer.Option("--reason")],
    by: str | None = None,
    revision: Revision = None,
    dry_run: DryRun = False,
):
    """Reject an article with a required reason."""
    invoke(ctx, editorial.decide, locals())


@app.command("inspect")
def inspect_article(ctx: typer.Context, id: Identifier):
    """Inspect an article and its publication blockers."""
    invoke(ctx, editorial.inspect_article, locals())


@app.command()
def classify(ctx: typer.Context, id: Identifier, file: Annotated[str, typer.Option("--file")]):
    """Replace classifications from evidence-backed operator JSON."""
    invoke(ctx, editorial.classify, locals())


@app.command("list")
def list_articles(
    ctx: typer.Context,
    limit: Limit = 50,
    after: UUID | None = None,
    review_status: ReviewStatus | None = None,
    publication_status: Literal["published", "unpublished"] | None = None,
):
    """List articles including unpublished candidates."""
    invoke(ctx, editorial.list_articles, locals())


@app.command("review-history")
def review_history(ctx: typer.Context, id: Identifier, limit: Limit = 50):
    """Inspect article editorial decisions."""
    invoke(ctx, editorial.history, locals())


@app.command("analyze", help="Queue AI analysis of an article.")
@app.command("analysis-retry", help="Retry a failed AI analysis job.")
def analyze(ctx: typer.Context, id: Identifier, force: Force = False):
    invoke(ctx, editorial.analyze, locals())


@app.command()
def analyses(ctx: typer.Context, article_id: UUID | None = None, limit: Limit = 50):
    """Inspect AI results, proposals and failures."""
    invoke(ctx, editorial.analyses, locals())


@app.command("analysis-dispatch")
def analysis_dispatch(ctx: typer.Context, id: Identifier):
    """Send a queued AI analysis job to RQ immediately."""
    invoke(ctx, lambda args: editorial.dispatch_analysis(args.id), locals())


@app.command("analysis-backfill")
def analysis_backfill(
    ctx: typer.Context,
    limit: Limit = 100,
    after: UUID | None = None,
    dispatch: Annotated[bool, typer.Option("--dispatch")] = False,
):
    """Queue a bounded batch of unreviewed article analyses."""
    invoke(ctx, editorial.analysis_backfill, locals())
