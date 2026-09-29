"""Only fixed public GET routes are available to tools; no caller credentials propagate."""

import json
from typing import Any

import anyio
import httpx
from mcp.server.mcpserver.exceptions import ToolError

from devfeed_mcp.config import Settings


class PublicAPI:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def get(self, path: str, **params: Any) -> Any:
        # Preserve repeated query parameters; httpx otherwise serializes None as an empty value.
        query = {key: value for key, value in params.items() if value is not None}
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
                    return json.loads(body)
        except (httpx.HTTPError, TimeoutError):
            raise ToolError("DevFeed is temporarily unavailable. Please retry later.") from None
        except (ValueError, UnicodeError):
            raise ToolError("DevFeed returned an invalid response. Please retry later.") from None
