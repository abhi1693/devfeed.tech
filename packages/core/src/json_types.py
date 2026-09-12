"""Extensible JSON metadata with a recursive, exportable JSON Schema."""

# Pydantic's built-in JsonValue emits an empty schema; clients need the full union.
type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
