"""Database-managed category/tag commands with explicit clearing controls."""

from typing import Annotated
from uuid import UUID

import typer

from devfeed_cli import commands
from devfeed_cli.options import Identifier, Limit, Offset, group, updates
from devfeed_cli.runtime import invoke

categories = group("Configure database-managed categories.")
tags = group("Configure database-managed tags.")
TopicID = Annotated[UUID | None, typer.Option("--topic-id")]
ClearTopic = Annotated[bool, typer.Option("--clear-topic")]
ParentID = Annotated[UUID | None, typer.Option("--parent-id")]
Root = Annotated[bool, typer.Option("--root")]
Keywords = Annotated[
    list[str] | None, typer.Option("--keyword", help="Repeat for multiple keywords.")
]
ClearKeywords = Annotated[bool, typer.Option("--clear-keywords")]
CategoryID = Annotated[UUID | None, typer.Option("--category-id")]
Ungroup = Annotated[bool, typer.Option("--ungroup")]
Aliases = Annotated[list[str] | None, typer.Option("--alias", help="Repeat for multiple aliases.")]
ClearAliases = Annotated[bool, typer.Option("--clear-aliases")]


@categories.command("list")
@tags.command("list")
def listing(ctx: typer.Context, limit: Limit = 100, offset: Offset = 0):
    """List stored taxonomy records."""
    invoke(ctx, commands.taxonomy_list, locals())


def write(ctx: typer.Context, parameters: dict, category: bool):
    fields = {field: field for field in ("name", "slug", "topic_id")}
    clear: dict[str, tuple[str, object]] = {"clear_topic": ("topic_id", None)}
    if category:
        fields.update(parent_id="parent_id", keyword="keywords")
        clear.update(root=("parent_id", None), clear_keywords=("keywords", []))
    else:
        fields.update(category_id="category_id", alias="aliases")
        clear.update(ungroup=("category_id", None), clear_aliases=("aliases", []))
    values = updates(ctx, parameters, fields, clear)
    if "id" in parameters:
        values["id"] = parameters["id"]
    invoke(ctx, commands.taxonomy_write, values)


@categories.command("add")
def add_category(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name")],
    slug: Annotated[str, typer.Option("--slug")],
    topic_id: TopicID = None,
    clear_topic: ClearTopic = False,
    parent_id: ParentID = None,
    root: Root = False,
    keyword: Keywords = None,
    clear_keywords: ClearKeywords = False,
):
    """Create a category, optionally nested under a parent."""
    write(ctx, locals(), True)


@categories.command("update")
def update_category(
    ctx: typer.Context,
    id: Identifier,
    name: str | None = None,
    slug: str | None = None,
    topic_id: TopicID = None,
    clear_topic: ClearTopic = False,
    parent_id: ParentID = None,
    root: Root = False,
    keyword: Keywords = None,
    clear_keywords: ClearKeywords = False,
):
    """Change only specified category fields."""
    write(ctx, locals(), True)


@tags.command("add")
def add_tag(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name")],
    slug: Annotated[str, typer.Option("--slug")],
    topic_id: TopicID = None,
    clear_topic: ClearTopic = False,
    category_id: CategoryID = None,
    ungroup: Ungroup = False,
    alias: Aliases = None,
    clear_aliases: ClearAliases = False,
):
    """Create a tag with optional aliases and category/topic links."""
    write(ctx, locals(), False)


@tags.command("update")
def update_tag(
    ctx: typer.Context,
    id: Identifier,
    name: str | None = None,
    slug: str | None = None,
    topic_id: TopicID = None,
    clear_topic: ClearTopic = False,
    category_id: CategoryID = None,
    ungroup: Ungroup = False,
    alias: Aliases = None,
    clear_aliases: ClearAliases = False,
):
    """Change only specified tag fields."""
    write(ctx, locals(), False)
