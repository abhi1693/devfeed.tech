"""Database-managed tag commands with explicit clearing controls."""

from typing import Annotated
from uuid import UUID

import typer

from devfeed_cli import commands
from devfeed_cli.options import Identifier, Limit, Offset, group, updates
from devfeed_cli.runtime import invoke

tags = group("Configure database-managed tags.")
TopicID = Annotated[UUID | None, typer.Option("--topic-id")]
ClearTopic = Annotated[bool, typer.Option("--clear-topic")]
Keywords = Annotated[
    list[str] | None, typer.Option("--keyword", help="Repeat for multiple keywords.")
]
ClearKeywords = Annotated[bool, typer.Option("--clear-keywords")]
Aliases = Annotated[list[str] | None, typer.Option("--alias", help="Repeat for multiple aliases.")]
ClearAliases = Annotated[bool, typer.Option("--clear-aliases")]


@tags.command("list")
def listing(ctx: typer.Context, limit: Limit = 100, offset: Offset = 0):
    """List stored taxonomy records."""
    invoke(ctx, commands.taxonomy_list, locals())


def write(ctx: typer.Context, parameters: dict):
    fields = {field: field for field in ("name", "slug", "topic_id")}
    clear: dict[str, tuple[str, object]] = {"clear_topic": ("topic_id", None)}
    fields.update(alias="aliases")
    clear.update(clear_aliases=("aliases", []))
    values = updates(ctx, parameters, fields, clear)
    if "id" in parameters:
        values["id"] = parameters["id"]
    invoke(ctx, commands.taxonomy_write, values)


@tags.command("add")
def add_tag(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name")],
    slug: Annotated[str, typer.Option("--slug")],
    topic_id: TopicID = None,
    clear_topic: ClearTopic = False,
    alias: Aliases = None,
    clear_aliases: ClearAliases = False,
):
    """Create a tag with optional aliases and topic links."""
    write(ctx, locals())


@tags.command("update")
def update_tag(
    ctx: typer.Context,
    id: Identifier,
    name: str | None = None,
    slug: str | None = None,
    topic_id: TopicID = None,
    clear_topic: ClearTopic = False,
    alias: Aliases = None,
    clear_aliases: ClearAliases = False,
):
    """Change only specified tag fields."""
    write(ctx, locals())
