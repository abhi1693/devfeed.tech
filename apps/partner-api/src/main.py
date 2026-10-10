"""Independently deployable partner API; public traffic never loads this service."""

import logging
from contextlib import asynccontextmanager

from devfeed_core.client_lifecycle import close_shared_clients
from devfeed_core.config import get_settings as core_settings
from devfeed_core.db import database_revision
from devfeed_core.logging import configure_logging
from devfeed_core.telemetry import start_runtime, stop_runtime
from devfeed_core.version import SCHEMA_REVISION, __version__
from devfeed_http.admission import AdmissionMiddleware
from devfeed_http.errors import register_error_handlers
from devfeed_http.health import readiness_response
from devfeed_http.logging import RequestLoggingMiddleware
from devfeed_http.schemas import ERROR_RESPONSES, HealthResponse, UnhealthyResponse
from devfeed_http.telemetry import fastapi_telemetry
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from devfeed_partner_api import auth, portal
from devfeed_partner_api.config import get_settings
from devfeed_partner_api.dependencies import DB, get_redis

logger = logging.getLogger(__name__)


def close_clients():
    close_shared_clients(get_redis)


@asynccontextmanager
async def lifespan(app):
    telemetry = start_runtime("partner-api")
    logger.info("partner_api_started")
    try:
        yield
    finally:
        await run_in_threadpool(close_clients)
        await run_in_threadpool(stop_runtime, telemetry)
        logger.info("partner_api_stopped")


def create_app() -> FastAPI:
    settings = core_settings()
    get_settings()  # Validate partner-only settings, without contacting the provider.
    configure_logging("partner-api", settings.log_level, settings.log_format, non_blocking=True)
    app = FastAPI(
        title="DevFeed Partner API",
        version=__version__,
        lifespan=lifespan,
        telemetry=fastapi_telemetry(),
        responses=ERROR_RESPONSES,
        description="Partner analytics API with role-gated sessions and live memberships.",
    )
    # No cross-origin cookie access: the Next.js partner service proxies same-origin requests.
    app.add_middleware(
        AdmissionMiddleware,
        requests=settings.api_max_concurrent_requests,
        streams=settings.api_max_concurrent_streams,
    )
    app.add_middleware(
        RequestLoggingMiddleware,
        service="partner-api",
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
    app.include_router(portal.router)
    return app


app = create_app()
