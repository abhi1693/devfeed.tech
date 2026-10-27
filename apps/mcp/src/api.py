"""Only fixed public GET routes are available to tools; no caller credentials propagate."""

import json
from collections.abc import Mapping, Sequence
from datetime import date
from typing import cast
from uuid import UUID

import anyio
import httpx
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import JsonValue

from devfeed_mcp.config import Settings

# HTTPX normalizes UUID/date query values through their string representation.
type QueryValue = str | int | float | bool | UUID | date | Sequence[str | UUID] | None


def query_parameters(
    values: Mapping[str, QueryValue],
) -> dict[str, str | int | float | bool | list[str]]:
    result: dict[str, str | int | float | bool | list[str]] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            result[key] = value
        elif isinstance(value, (UUID, date)):
            result[key] = str(value)
        else:
            result[key] = [str(item) for item in value]
    return result


def json_array(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ToolError("DevFeed returned an invalid response. Please retry later.")
    return value


class PublicAPI:
    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    async def get(self, path: str, **params: QueryValue) -> JsonValue:
        # Preserve repeated query parameters; httpx otherwise serializes None as an empty value.
        query = query_parameters(params)
        try:
            # Include pool wait and the whole streamed body in the deadline.
            with anyio.fail_after(self.settings.timeout_seconds):
                async with self.client.stream("GET", path, params=query) as response:
                    if response.status_code == 404:
                        raise ToolError("Public record not found.")
                    if response.status_code == 422:
                        raise ToolError("Invalid filters or cursor. Check the tool parameters.")
                    if not response.is_success:
                        raise ToolError("DevFeed is temporarily unavailable. Please retry later.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.settings.max_response_bytes:
                            raise ToolError("DevFeed response is too large. Use a smaller page.")
                    # json.loads produces only the recursive JSON value types.
                    return cast(JsonValue, json.loads(body))
        except (httpx.HTTPError, TimeoutError):
            raise ToolError("DevFeed is temporarily unavailable. Please retry later.") from None
        except (ValueError, UnicodeError):
            raise ToolError("DevFeed returned an invalid response. Please retry later.") from None
