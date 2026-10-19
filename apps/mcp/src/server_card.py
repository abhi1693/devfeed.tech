"""Public cards reflect the same initialization metadata as the live transport."""

from mcp.server.models import InitializationOptions
from starlette.requests import Request
from starlette.responses import JSONResponse

from devfeed_mcp.config import Settings


def server_card(options: InitializationOptions, settings: Settings):
    capabilities = options.capabilities.model_dump(mode="json", by_alias=True, exclude_none=True)
    # The SDK omits empty extension bookkeeping from its live initialize response.
    if not capabilities.get("extensions"):
        capabilities.pop("extensions", None)

    async def card(request: Request):
        headers = {"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=300"}
        if not settings.public_url:
            return JSONResponse({"detail": "Public MCP endpoint is not configured"}, 404, headers)
        return JSONResponse(
            {
                "$schema": "https://static.modelcontextprotocol.io/schemas/v1/server-card.schema.json",
                "name": "tech.devfeed/mcp",
                "title": options.server_name,
                "description": (
                    "Discover developer articles, topics and sources "
                    "with optional OAuth account tools."
                ),
                "version": options.server_version,
                "remotes": [{"type": "streamable-http", "url": settings.public_url}],
                # Compatibility fields for consumers of the earlier server-card proposal.
                "serverInfo": {"name": options.server_name, "version": options.server_version},
                "endpoint": settings.public_url,
                "transport": "streamable-http",
                "capabilities": capabilities,
            },
            headers=headers,
            media_type="application/mcp-server-card+json",
        )

    return card
