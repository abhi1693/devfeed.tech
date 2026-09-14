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


def _grant_decision_budget(args):
    from devfeed_core.db import session_factory
    from devfeed_core.topic_decision_budget import grant_budget

    with session_factory().begin() as session:
        return grant_budget(
            session,
            args.id,
            calls=args.calls,
            tokens=args.tokens,
            note=args.reason,
            actor=args.actor,
        )


@app.command("grant-decision-budget")
def grant_decision_budget(
    ctx: typer.Context,
    id: Identifier,
    reason: Annotated[str, typer.Option("--reason")],
    actor: Annotated[str, typer.Option("--actor")],
    calls: Annotated[int, typer.Option(min=1, max=5)] = 3,
    tokens: Annotated[int, typer.Option(min=2000, max=64000)] = 32000,
):
    """Explicitly restart a deferred review with additional, audited capacity."""
    invoke(ctx, _grant_decision_budget, locals())
