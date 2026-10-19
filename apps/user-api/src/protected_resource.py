"""Public discovery for the MCP bearer-token resource, not browser sessions."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from devfeed_user_api.config import get_settings

router = APIRouter(tags=["discovery"])


@router.get("/.well-known/oauth-protected-resource", operation_id="reader_protected_resource")
def metadata():
    settings = get_settings()
    headers = {"Cache-Control": "no-store"}
    if not settings.mcp_resource_url or not settings.mcp_issuer_url:
        return JSONResponse({"detail": "Agent authentication is not configured"}, 404, headers)
    return JSONResponse(
        {
            "resource": settings.mcp_resource_url,
            "authorization_servers": [settings.mcp_issuer_url],
            "bearer_methods_supported": ["header"],
        },
        headers=headers,
    )
