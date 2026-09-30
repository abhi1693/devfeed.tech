from contextlib import asynccontextmanager
from importlib.metadata import version

import httpx
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.routes import build_metadata, cors_middleware, create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from redis.asyncio import Redis
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp

from devfeed_mcp.api import PublicAPI
from devfeed_mcp.config import Settings
from devfeed_mcp.oauth import RESOURCE_SCOPES, SCOPES, OAuthPolicyMiddleware, OAuthProvider
from devfeed_mcp.personal import AccountToolAuth, UserAPI, register_personal_tools
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

    personal_server = None
    redis = None
    user_client = None
    personal_app: ASGIApp | None = None
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
        personal_server = create_server(
            api,
            auth_server_provider=provider,
            auth=AuthSettings(
                issuer_url=settings.oauth_issuer_url,
                resource_server_url=settings.public_url,
                validate_token_resource=True,
                required_scopes=[SCOPES[0]],
                client_registration_options=ClientRegistrationOptions(
                    enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES
                ),
                revocation_options=RevocationOptions(enabled=True),
            ),
        )
        register_personal_tools(personal_server, UserAPI(user_client, settings))
        private_transport = personal_server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            max_request_body_size=32768,
            transport_security=TransportSecuritySettings(
                allowed_hosts=settings.allowed_hosts, allowed_origins=settings.allowed_origins
            ),
        )

        for route in private_transport.router.routes:
            if isinstance(route, Route) and route.path == "/mcp":
                private_transport.router.routes[private_transport.router.routes.index(route)] = (
                    Route("/mcp", endpoint=AccountToolAuth(route.endpoint))
                )
                break

        # Advertise optional write permission while requiring only read permission.
        assert personal_server.settings.auth
        assert personal_server.settings.auth.resource_server_url
        resource_routes = create_protected_resource_routes(
            personal_server.settings.auth.resource_server_url,
            [personal_server.settings.auth.issuer_url],
            scopes_supported=RESOURCE_SCOPES,
        )
        auth_settings = personal_server.settings.auth
        assert auth_settings.client_registration_options
        assert auth_settings.revocation_options
        metadata = build_metadata(
            auth_settings.issuer_url,
            auth_settings.service_documentation_url,
            client_registration_options=auth_settings.client_registration_options,
            revocation_options=auth_settings.revocation_options,
        )
        metadata.token_endpoint_auth_methods_supported = ["none"]
        metadata.revocation_endpoint_auth_methods_supported = ["none"]
        metadata_route = Route(
            "/.well-known/oauth-authorization-server",
            endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        )
        private_transport.router.routes = (
            [metadata_route]
            + resource_routes
            + [
                route
                for route in private_transport.router.routes
                if not getattr(route, "path", "").startswith(
                    "/.well-known/oauth-protected-resource"
                )
                and getattr(route, "path", "") != "/.well-known/oauth-authorization-server"
            ]
        )
        personal_app = OAuthPolicyMiddleware(private_transport, settings.public_url)

    async def dispatch(scope, receive, send):
        if (
            settings.web_url
            and scope["type"] == "http"
            and scope["method"] in {"GET", "HEAD"}
            and scope["path"].rstrip("/") == "/mcp"
        ):
            request = Request(scope)
            accepted_types = {}
            for media_type in request.headers.get("accept", "").lower().split(","):
                name, *parameters = media_type.split(";")
                quality = 1.0
                for parameter in parameters:
                    key, _, value = parameter.strip().partition("=")
                    if key == "q":
                        try:
                            quality = float(value)
                        except ValueError:
                            quality = 0.0
                accepted_types[name.strip()] = quality
            if (
                accepted_types.get("text/html", 0) > 0
                and accepted_types.get("text/event-stream", 0) <= 0
                and "mcp-protocol-version" not in request.headers
            ):
                response = RedirectResponse(
                    settings.web_url.rstrip("/") + "/mcp",
                    status_code=302,
                    headers={"Cache-Control": "no-store", "Vary": "Accept, Mcp-Protocol-Version"},
                )
                await response(scope, receive, send)
                return
        app = personal_app or mcp_app
        await app(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app):
        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(client)
            await stack.enter_async_context(server.session_manager.run())
            if personal_server and user_client and redis:
                await stack.enter_async_context(user_client)
                await stack.enter_async_context(personal_server.session_manager.run())
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

    return Starlette(
        routes=[
            Route("/health/live", live),
            Route("/health/ready", ready),
            Route("/version", app_version),
            Mount("/", app=dispatch),
        ],
        lifespan=lifespan,
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=settings.allowed_origins,
                allow_methods=["GET", "POST", "DELETE"],
                allow_headers=[
                    "Content-Type",
                    "Mcp-Protocol-Version",
                    "Mcp-Method",
                    "Mcp-Name",
                    "Authorization",
                ],
                expose_headers=["WWW-Authenticate"],
            )
        ],
    )


app = create_app()
