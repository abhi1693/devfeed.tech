"""Independently deployable user API; public traffic never loads this service."""

import logging
from contextlib import asynccontextmanager

from devfeed_core.cache import close_cache
from devfeed_core.config import get_settings as core_settings
from devfeed_core.db import database_revision, get_engine
from devfeed_core.logging import configure_logging
from devfeed_core.version import SCHEMA_REVISION, __version__
from devfeed_http.errors import register_error_handlers
from devfeed_http.logging import RequestLoggingMiddleware
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from devfeed_user_api import (
    auth,
    engagement,
    notifications,
    preferences,
    profile,
    recommendations,
    sources,
)
from devfeed_user_api.config import get_settings
from devfeed_user_api.dependencies import DB, get_redis

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    logger.info("user_api_started")
    try:
        yield
    finally:
        close_cache()
        if get_engine.cache_info().currsize:
            get_engine().dispose()
        if get_redis.cache_info().currsize:
            get_redis().close()
        get_redis.cache_clear()
        logger.info("user_api_stopped")


def create_app() -> FastAPI:
    settings = core_settings()
    get_settings()  # Validate user-only settings, without contacting the provider.
    configure_logging("user-api", settings.log_level, settings.log_format)
    app = FastAPI(
        title="DevFeed User API",
        version=__version__,
        lifespan=lifespan,
        description="Optional personalization API. OIDC sessions and CSRF protection required.",
    )
    # No cross-origin cookie access: the Next.js user service proxies same-origin requests.
    app.add_middleware(
        RequestLoggingMiddleware,
        service="user-api",
        logger=logger,
        response_headers=(
            (b"cache-control", b"no-store"),
            (b"referrer-policy", b"no-referrer"),
        ),
    )

    register_error_handlers(app, logger, admin=True)

    @app.get("/health/live", tags=["health"])
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready(session: DB):
        try:
            revision = database_revision(session)
            if revision != SCHEMA_REVISION:
                return JSONResponse({"status": "migration_required"}, status_code=503)
            get_redis().ping()
        except (SQLAlchemyError, RedisError):
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(preferences.router)
    app.include_router(engagement.router)
    app.include_router(notifications.router)
    app.include_router(profile.router)
    app.include_router(recommendations.router)
    app.include_router(sources.router)
    return app


app = create_app()
