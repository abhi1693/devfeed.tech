"""Independently deployable user API; public traffic never loads this service."""

import logging

from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.client_lifecycle import close_shared_clients
from devfeed_core.config import get_settings as core_settings
from devfeed_core.db import database_revision
from devfeed_core.logging import configure_logging
from devfeed_core.version import SCHEMA_REVISION, __version__
from devfeed_http.analytics import analytics_resources
from devfeed_http.operation_policies import (
    ExtensionTransportPolicy,
    ReaderGatewayPolicy,
    include_gateway_router,
)
from devfeed_http.route_policies import install_runtime_policies
from devfeed_http.schemas import ERROR_RESPONSES
from devfeed_http.service import HTTPService
from devfeed_http.telemetry import fastapi_telemetry
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from devfeed_user_api import (
    auth,
    avatars,
    bookmarks,
    engagement,
    leaderboard,
    mcp,
    must_reads,
    notifications,
    oidc_discovery,
    partner_placements,
    preferences,
    profile,
    protected_resource,
    recommendations,
    sources,
)
from devfeed_user_api.config import get_settings
from devfeed_user_api.dependencies import database_session, get_redis

logger = logging.getLogger(__name__)


def close_clients() -> None:
    close_shared_clients(get_redis)


service = HTTPService("user-api", logger, lambda: close_clients(), analytics_resources)
lifespan = service.lifespan


def create_app() -> FastAPI:
    settings = core_settings()
    get_settings()  # Validate user-only settings, without contacting the provider.
    configure_logging("user-api", settings.log_level, settings.log_format, non_blocking=True)
    app = FastAPI(
        title="DevFeed User API",
        version=__version__,
        lifespan=lifespan,
        telemetry=fastapi_telemetry(),
        responses=ERROR_RESPONSES,
        description="Optional personalization API. OIDC sessions and CSRF protection required.",
    )
    # No cross-origin cookie access: the Next.js user service proxies same-origin requests.
    service.configure(app, settings, private=True)

    @app.exception_handler(AnalyticsUnavailable)
    async def analytics_unavailable(
        _request: Request, _error: AnalyticsUnavailable
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "Analytics unavailable"})

    service.register_health(
        app,
        session_dependency=database_session,
        get_redis=lambda: get_redis(),
        revision_reader=lambda session: database_revision(session),
        schema_revision=SCHEMA_REVISION,
    )

    include_gateway_router(app, auth.router, default_policy=ReaderGatewayPolicy())
    for router in (
        mcp.router,
        preferences.router,
        engagement.router,
        bookmarks.router,
        notifications.router,
        profile.router,
        profile.public_router,
        avatars.router,
        leaderboard.router,
        recommendations.router,
        partner_placements.router,
        must_reads.router,
        sources.router,
    ):
        include_gateway_router(
            app, router, default_policy=ReaderGatewayPolicy(extension=ExtensionTransportPolicy())
        )
    app.include_router(protected_resource.router)
    app.include_router(oidc_discovery.router)
    install_runtime_policies(app)
    return app


app = create_app()
