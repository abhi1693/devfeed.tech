"""Independently deployable user API; public traffic never loads this service."""

import logging
from contextlib import asynccontextmanager

from devfeed_core.cache import close_cache
from devfeed_core.config import get_settings as core_settings
from devfeed_core.db import database_revision, get_engine
from devfeed_core.logging import configure_logging
from devfeed_core.telemetry import start_runtime, stop_runtime
from devfeed_core.version import SCHEMA_REVISION, __version__
from devfeed_http.admission import AdmissionMiddleware
from devfeed_http.errors import register_error_handlers
from devfeed_http.health import readiness_response
from devfeed_http.logging import RequestLoggingMiddleware
from devfeed_http.schemas import ERROR_RESPONSES, HealthResponse, UnhealthyResponse
from devfeed_http.telemetry import TelemetryMiddleware
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from devfeed_user_api import (
    auth,
    bookmarks,
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


def close_clients():
    close_cache()
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    if get_redis.cache_info().currsize:
        get_redis().close()
    get_redis.cache_clear()


@asynccontextmanager
async def lifespan(app):
    telemetry = start_runtime("user-api")
    logger.info("user_api_started")
    try:
        yield
    finally:
        await run_in_threadpool(close_clients)
        await run_in_threadpool(stop_runtime, telemetry)
        logger.info("user_api_stopped")


def create_app() -> FastAPI:
    settings = core_settings()
    get_settings()  # Validate user-only settings, without contacting the provider.
    configure_logging("user-api", settings.log_level, settings.log_format, non_blocking=True)
    app = FastAPI(
        title="DevFeed User API",
        version=__version__,
        lifespan=lifespan,
        responses=ERROR_RESPONSES,
        description="Optional personalization API. OIDC sessions and CSRF protection required.",
    )
    # No cross-origin cookie access: the Next.js user service proxies same-origin requests.
    app.add_middleware(
        AdmissionMiddleware,
        requests=settings.api_max_concurrent_requests,
        streams=settings.api_max_concurrent_streams,
    )
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

    @app.get("/health/live", tags=["health"], response_model=HealthResponse)
    async def live():
        return {"status": "ok"}

    @app.get(
        "/health/ready",
        tags=["health"],
        response_model=HealthResponse,
        responses={503: {"model": UnhealthyResponse, "description": "Not ready"}},
    )
    def ready(session: DB):
        return readiness_response(
            session,
            get_redis(),
            revision_reader=database_revision,
            schema_revision=SCHEMA_REVISION,
        )

    app.include_router(auth.router)
    app.include_router(preferences.router)
    app.include_router(engagement.router)
    app.include_router(bookmarks.router)
    app.include_router(notifications.router)
    app.include_router(profile.router)
    app.include_router(profile.public_router)
    app.include_router(recommendations.router)
    app.include_router(sources.router)
    app.add_middleware(TelemetryMiddleware)
    return app


app = create_app()
