from typing import Annotated, Literal
from uuid import UUID

import typer

from devfeed_cli import editorial
from devfeed_cli.options import Identifier, Limit, group
from devfeed_cli.runtime import invoke

app = group("Manage canonical developer topics.")


@app.command("list")
def list_topics(ctx: typer.Context, limit: Limit = 100):
    """List topic profiles."""
    invoke(ctx, editorial.topic_list, locals())


@app.command()
def add(ctx: typer.Context, file: Annotated[str, typer.Option("--file")]):
    """Create a topic profile from JSON."""
    invoke(ctx, editorial.topic_write, locals())


@app.command()
def update(ctx: typer.Context, id: Identifier, file: Annotated[str, typer.Option("--file")]):
    """Update a topic profile from JSON."""
    invoke(ctx, editorial.topic_write, locals())


@app.command()
def relate(
    ctx: typer.Context,
    id: Identifier,
    related_id: UUID,
    relation: Annotated[
        Literal["uses_language", "depends_on", "implements", "part_of", "related_to"],
        typer.Option("--relation"),
    ],
    evidence_url: str | None = None,
):
    """Link two topics with a typed relationship."""
    invoke(ctx, editorial.topic_relate, locals())


@app.command()
def accept(
    ctx: typer.Context,
    analysis_id: UUID,
    slug: Annotated[str, typer.Option("--slug")],
):
    """Accept a proposed identity from an analysis result."""
    invoke(ctx, editorial.topic_accept, locals())
