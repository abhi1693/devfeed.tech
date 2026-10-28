"""Extensible JSON metadata with a recursive, exportable JSON Schema."""

from typing import Any

# Pydantic's built-in JsonValue emits an empty schema; clients need the full union.
type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]

# JSONB columns retain multiple historical versions and extension/provider payloads.
# Their values are validated by the owning domain when read; preserving this dynamic
# persistence boundary must not erase the concrete contracts of application APIs.
type JsonRecord = dict[str, Any]
