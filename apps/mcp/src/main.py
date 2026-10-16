from contextlib import asynccontextmanager
from importlib.metadata import version

import httpx
from redis.asyncio import Redis
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from devfeed_mcp.api import PublicAPI
from devfeed_mcp.config import Settings
from devfeed_mcp.oauth import OAuthProvider
from devfeed_mcp.personal import UserAPI, register_personal_tools
from devfeed_mcp.server import create_server
from devfeed_mcp.transport import MCPRoutes


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
    redis = None
    user_client = None
    provider = None
    if settings.oauth_issuer_url:
        assert (
            settings.public_url
            and settings.redis_url
            and settings.user_api_url
            and settings.oauth_issuer_url
            and settings.web_url
        )
        redis = Redis.from_url(settings.redis_url.get_secret_value())
        provider = OAuthProvider(
            redis, settings.public_url, settings.web_url, settings.access_token_ttl_seconds
        )
        user_client = httpx.AsyncClient(
            base_url=settings.user_api_url.rstrip("/") + "/",
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            limits=httpx.Limits(max_connections=settings.max_connections),
        )
        register_personal_tools(server, UserAPI(user_client, settings))

    mcp_routes = MCPRoutes(server, settings, provider)

    @asynccontextmanager
    async def lifespan(app):
        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(client)
            await stack.enter_async_context(server.session_manager.run())
            if user_client and redis:
                await stack.enter_async_context(user_client)
                stack.push_async_callback(redis.aclose)
            yield

    async def live(request: Request):
        return JSONResponse({"status": "ok"})

    async def ready(request: Request):
        try:
            await api.get("v1/topics", limit=1)
            if redis and user_client:
                import anyio

                with anyio.fail_after(settings.timeout_seconds):
                    await redis.ping()
                    response = await user_client.get("health/ready")
                    response.raise_for_status()
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    async def app_version(request: Request):
        return JSONResponse({"version": version("devfeed-mcp")})

    app = Starlette(
        routes=[
            Route("/health/live", live),
            Route("/health/ready", ready),
            Route("/version", app_version),
            Mount("/", app=mcp_routes.app),
        ],
        lifespan=lifespan,
    )
    app.state.mcp_routes = mcp_routes
    return app


app = create_app()
