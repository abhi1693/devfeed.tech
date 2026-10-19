"""Public discovery for the MCP bearer-token resource, not browser sessions."""

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from devfeed_user_api.config import get_settings

router = APIRouter(tags=["discovery"])


class ProtectedResourceMetadataResponse(BaseModel):
    resource: str
    authorization_servers: list[str]
    bearer_methods_supported: list[Literal["header"]]


@router.get(
    "/.well-known/oauth-protected-resource",
    operation_id="reader_protected_resource",
    response_model=ProtectedResourceMetadataResponse,
)
def metadata() -> JSONResponse:
    settings = get_settings()
    headers = {"Cache-Control": "no-store"}
    if not settings.mcp_resource_url or not settings.mcp_issuer_url:
        return JSONResponse({"detail": "Agent authentication is not configured"}, 404, headers)
    return JSONResponse(
        ProtectedResourceMetadataResponse(
            resource=settings.mcp_resource_url,
            authorization_servers=[settings.mcp_issuer_url],
            bearer_methods_supported=["header"],
        ).model_dump(),
        headers=headers,
    )
