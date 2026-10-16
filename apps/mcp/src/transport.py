"""Bind HTTP policies to SDK routes while retaining the SDK transport lifecycle."""

from collections.abc import Awaitable, Callable

from mcp.server import MCPServer
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.revoke import RevocationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import (
    AUTHORIZATION_PATH,
    REGISTRATION_PATH,
    REVOCATION_PATH,
    TOKEN_PATH,
    build_metadata,
    build_resource_metadata_url,
    cors_middleware,
    create_protected_resource_routes,
    validate_issuer_url,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route, request_response
from starlette.types import ASGIApp, Receive, Scope, Send

from devfeed_mcp.config import Settings
from devfeed_mcp.oauth import RESOURCE_SCOPES, SCOPES, OAuthProvider, OAuthRequestPolicy
from devfeed_mcp.personal import AccountToolAuth

OAuthHandler = Callable[[Request], Response | Awaitable[Response]]


class BrowserNegotiation:
    """Offer reader instructions only on the bound MCP transport operation."""

    def __init__(self, app: ASGIApp, web_url: str):
        self.app = app
        self.destination = web_url.rstrip("/") + "/mcp"

    @staticmethod
    def wants_instructions(request: Request) -> bool:
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
        return (
            accepted_types.get("text/html", 0) > 0
            and accepted_types.get("text/event-stream", 0) <= 0
            and "mcp-protocol-version" not in request.headers
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in {"GET", "HEAD"}
            and self.wants_instructions(Request(scope))
        ):
            await RedirectResponse(
                self.destination,
                status_code=302,
                headers={"Cache-Control": "no-store", "Vary": "Accept, Mcp-Protocol-Version"},
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


class OAuthRoutes:
    """Construct named OAuth operations from SDK handlers and explicit policies."""

    def __init__(self, provider: OAuthProvider, settings: AuthSettings):
        validate_issuer_url(settings.issuer_url)
        assert settings.resource_server_url
        registration = settings.client_registration_options
        revocation = settings.revocation_options
        assert registration and registration.enabled
        assert revocation and revocation.enabled
        authenticator = ClientAuthenticator(provider)
        metadata = build_metadata(
            settings.issuer_url,
            settings.service_documentation_url,
            registration,
            revocation,
            supports_identity_assertion=settings.identity_assertion_enabled,
        )
        metadata.token_endpoint_auth_methods_supported = ["none"]
        metadata.revocation_endpoint_auth_methods_supported = ["none"]
        self.metadata = Route(
            "/.well-known/oauth-authorization-server",
            endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        )
        self.authorization = Route(
            AUTHORIZATION_PATH,
            endpoint=OAuthRequestPolicy(request_response(AuthorizationHandler(provider).handle)),
            methods=["GET", "POST"],
        )
        self.token = self.client_route(
            TOKEN_PATH,
            TokenHandler(
                provider,
                authenticator,
                identity_assertion_enabled=settings.identity_assertion_enabled,
            ).handle,
            resource=str(settings.resource_server_url),
        )
        self.registration = self.client_route(
            REGISTRATION_PATH, RegistrationHandler(provider, options=registration).handle
        )
        self.revocation = self.client_route(
            REVOCATION_PATH, RevocationHandler(provider, authenticator).handle
        )
        self.protected_resources = create_protected_resource_routes(
            settings.resource_server_url,
            [settings.issuer_url],
            scopes_supported=RESOURCE_SCOPES,
        )

    @staticmethod
    def client_route(path: str, handler: OAuthHandler, *, resource: str | None = None) -> Route:
        # CORS must cover responses produced by the body/resource policy as well as the SDK.
        endpoint = CORSMiddleware(
            OAuthRequestPolicy(request_response(handler), resource),
            allow_origins=["*"],
            allow_methods=["POST", "OPTIONS"],
            allow_headers=["Mcp-Protocol-Version"],
        )
        return Route(path, endpoint=endpoint, methods=["POST", "OPTIONS"])

    @property
    def routes(self) -> list[Route]:
        return [
            self.metadata,
            self.authorization,
            self.token,
            self.registration,
            self.revocation,
            *self.protected_resources,
        ]


class MCPRoutes:
    """Wrap the SDK's actual transport route and add named OAuth operations."""

    def __init__(
        self, server: MCPServer, settings: Settings, provider: OAuthProvider | None = None
    ):
        self.app = server.streamable_http_app(
            json_response=True,
            stateless_http=True,
            max_request_body_size=32_768,
            transport_security=TransportSecuritySettings(
                allowed_hosts=settings.allowed_hosts, allowed_origins=settings.allowed_origins
            ),
        )
        # An unauthenticated SDK app has exactly one transport route. Capture its identity,
        # rather than searching a URL or depending on the internals of an auth wrapper.
        if len(self.app.routes) != 1 or not isinstance(self.app.routes[0], Route):
            raise RuntimeError("Expected one SDK MCP transport route before binding policies")
        self.transport = self.app.routes[0]
        endpoint = self.transport.app
        self.oauth: OAuthRoutes | None = None
        if provider is not None:
            assert settings.oauth_issuer_url and settings.public_url
            auth = AuthSettings.model_validate(
                {
                    "issuer_url": settings.oauth_issuer_url,
                    "resource_server_url": settings.public_url,
                    "validate_token_resource": True,
                    "required_scopes": [SCOPES[0]],
                    "client_registration_options": ClientRegistrationOptions(
                        enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES
                    ),
                    "revocation_options": RevocationOptions(enabled=True),
                }
            )
            self.oauth = OAuthRoutes(provider, auth)
            assert auth.resource_server_url
            required_auth = RequireAuthMiddleware(
                endpoint,
                auth.required_scopes or [],
                build_resource_metadata_url(auth.resource_server_url),
            )
            endpoint = AuthenticationMiddleware(
                AuthContextMiddleware(AccountToolAuth(endpoint, required_auth)),
                backend=BearerAuthBackend(
                    ProviderTokenVerifier(provider), resource_server_url=auth.resource_server_url
                ),
            )
            self.app.router.routes = [*self.oauth.routes, self.transport]
        if settings.web_url:
            endpoint = BrowserNegotiation(endpoint, settings.web_url)
        self.transport.app = CORSMiddleware(
            endpoint,
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
