"""Independently deployable user API; public traffic never loads this service."""

import logging

from devfeed_core.client_lifecycle import close_shared_clients
from devfeed_core.config import get_settings as core_settings
from devfeed_core.db import database_revision
from devfeed_core.logging import configure_logging
from devfeed_core.version import SCHEMA_REVISION, __version__
from devfeed_core.web_push import get_web_push_settings
from devfeed_http.schemas import ERROR_RESPONSES
from devfeed_http.service import HTTPService
from devfeed_http.telemetry import fastapi_telemetry
from fastapi import FastAPI

from devfeed_user_api import (
    auth,
    avatars,
    bookmarks,
    engagement,
    leaderboard,
    mcp,
    must_reads,
    notifications,
    preferences,
    profile,
    recommendations,
    sources,
    web_push,
)
from devfeed_user_api.config import get_settings
from devfeed_user_api.dependencies import database_session, get_redis

logger = logging.getLogger(__name__)


def close_clients():
    close_shared_clients(get_redis)


service = HTTPService("user-api", logger, lambda: close_clients())
lifespan = service.lifespan


def create_app() -> FastAPI:
    settings = core_settings()
    get_settings()  # Validate user-only settings, without contacting the provider.
    get_web_push_settings()  # Public VAPID configuration never loads the signing credential.
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
    service.configure(
        app,
        settings,
        private=True,
        middleware=lambda app: app.add_middleware(avatars.AvatarUploadBodyLimit),
    )

    service.register_health(
        app,
        session_dependency=database_session,
        get_redis=lambda: get_redis(),
        revision_reader=lambda session: database_revision(session),
        schema_revision=SCHEMA_REVISION,
    )

    app.include_router(auth.router)
    app.include_router(mcp.router)
    app.include_router(preferences.router)
    app.include_router(engagement.router)
    app.include_router(bookmarks.router)
    app.include_router(notifications.router)
    app.include_router(web_push.router)
    app.include_router(profile.router)
    app.include_router(profile.public_router)
    app.include_router(avatars.router)
    app.include_router(leaderboard.router)
    app.include_router(recommendations.router)
    app.include_router(must_reads.router)
    app.include_router(sources.router)
    return app


app = create_app()
