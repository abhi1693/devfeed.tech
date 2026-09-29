from contextlib import asynccontextmanager
from importlib.metadata import version

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from devfeed_mcp.api import PublicAPI
from devfeed_mcp.config import Settings
from devfeed_mcp.server import create_server


def create_app(
    settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> Starlette:
    settings = settings or Settings()
    client = httpx.AsyncClient(
        base_url=settings.api_url + "/",
        timeout=settings.timeout_seconds,
        limits=httpx.Limits(max_connections=settings.max_connections),
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    )
    api = PublicAPI(client, settings)
    server = create_server(api)
    mcp_app = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        max_request_body_size=32_768,
        transport_security=TransportSecuritySettings(
            allowed_hosts=settings.allowed_hosts, allowed_origins=settings.allowed_origins
        ),
    )

    @asynccontextmanager
    async def lifespan(app):
        async with client, server.session_manager.run():
            yield

    async def live(request: Request):
        return JSONResponse({"status": "ok"})

    async def ready(request: Request):
        try:
            await api.get("v1/topics", limit=1)
        except ToolError:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    async def app_version(request: Request):
        return JSONResponse({"version": version("devfeed-mcp")})

    return Starlette(
        routes=[
            Route("/health/live", live),
            Route("/health/ready", ready),
            Route("/version", app_version),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=settings.allowed_origins,
                allow_methods=["GET", "POST", "DELETE"],
                allow_headers=["Content-Type", "Mcp-Protocol-Version", "Mcp-Method", "Mcp-Name"],
            )
        ],
    )


app = create_app()
