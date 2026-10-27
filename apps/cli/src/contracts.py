"""Operator output contracts and the validated CLI adapter boundary."""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

# Operator reports also contain native timestamps/identities; the renderer uses
# json.dumps(default=str). Public JSON response contracts remain stricter.
type CommandValue = (
    str
    | int
    | float
    | bool
    | None
    | datetime
    | UUID
    | Sequence[CommandValue]
    | Mapping[str, CommandValue]
)
type CommandOutput = Mapping[str, CommandValue]
# The renderer deliberately accepts arbitrary operator values: default=str
# also handles SDK/dataclass objects. Keep that opaque value at this output
# boundary; command handlers retain their own concrete return types.
type CommandResult = object

# Typer validates each command's own fields before adapting its parameters to a
# namespace. Its dynamic attribute bag is confined to this operator adapter;
# domain services receive the concrete extracted values, never the namespace.
type CommandHandler = Callable[[SimpleNamespace], CommandResult]
