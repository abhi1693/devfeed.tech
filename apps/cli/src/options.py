"""Reusable typed options and explicit update/clear semantics."""

from typing import Annotated, Literal
from uuid import UUID

import typer

Limit = Annotated[int, typer.Option(min=1, max=500, help="Maximum records to process or return.")]
Offset = Annotated[int, typer.Option(min=0, max=2_147_483_647)]
Identifier = Annotated[UUID, typer.Argument(help="Record UUID.")]
Force = Annotated[bool, typer.Option("--force", help="Dispatch now; never steal running jobs.")]
PollInterval = Annotated[
    int, typer.Option("--poll-interval", min=300, max=604800, metavar="SECONDS")
]
JobStatus = Literal["queued", "running", "succeeded", "failed"]
ReviewStatus = Literal["pending", "approved", "rejected"]


def group(help_text: str) -> typer.Typer:
    return typer.Typer(
        help=help_text,
        no_args_is_help=True,
        add_completion=False,
        pretty_exceptions_enable=False,
        pretty_exceptions_show_locals=False,
        context_settings={"help_option_names": ["-h", "--help"]},
    )


def boolean_pair(positive: bool, negative: bool, flags: str) -> bool | None:
    if positive and negative:
        raise typer.BadParameter(f"Cannot combine {flags}.")
    return True if positive else False if negative else None


def updates(
    ctx: typer.Context,
    parameters: dict,
    fields: dict[str, str],
    clear: dict[str, tuple[str, object]] | None = None,
) -> dict:
    """Do not confuse an omitted option with an intentional null/empty value."""
    values = {
        target: parameters[name]
        for name, target in fields.items()
        if getattr(ctx.get_parameter_source(name), "name", None) == "COMMANDLINE"
    }
    for flag, (target, empty) in (clear or {}).items():
        if parameters[flag]:
            if target in values:
                option = next(name for name, field in fields.items() if field == target)
                raise typer.BadParameter(
                    f"Cannot combine --{option.replace('_', '-')} and --{flag.replace('_', '-')}.",
                )
            values[target] = empty
    return values
