"""Independently deployable admin API; public traffic never loads this service."""

import asyncio
import logging
from contextlib import asynccontextmanager

from devfeed_core.cache import close_cache
from devfeed_core.config import get_settings as core_settings
from devfeed_core.db import database_revision, get_engine
from devfeed_core.logging import configure_logging
from devfeed_core.telemetry import start_runtime, stop_runtime
from devfeed_core.version import BACKWARD_COMPATIBLE_SCHEMA_REVISIONS, SCHEMA_REVISION, __version__
from devfeed_http.errors import register_error_handlers
from devfeed_http.logging import RequestLoggingMiddleware
from devfeed_http.schemas import ERROR_RESPONSES, HealthResponse, UnhealthyResponse
from devfeed_http.telemetry import TelemetryMiddleware
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from devfeed_admin_api import (
    ai_connection,
    articles,
    auth,
    automation,
    ingestion,
    jobs,
    notifications,
    overview,
    overview_panels,
    source_imports,
    sources,
    taxonomy,
    topic_proposals,
    topic_relationships,
    topic_replacements,
    topics,
    user_settings,
    users,
    workers,
)
from devfeed_admin_api.codex_connection import CodexConnection
from devfeed_admin_api.config import get_settings
from devfeed_admin_api.dependencies import DB, get_redis
from devfeed_admin_api.reporting import close_reporting

logger = logging.getLogger(__name__)


def close_clients():
    close_reporting()
    close_cache()
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    if get_redis.cache_info().currsize:
        get_redis().close()
    get_redis.cache_clear()


@asynccontextmanager
async def lifespan(app):
    telemetry = start_runtime("admin-api")
    logger.info("admin_api_started")
    app.state.codex.start()
    try:
        yield
    finally:
        await overview.close_snapshot_tasks(app)
        await overview_panels.close_panel_tasks(app)
        await app.state.codex.close()
        await run_in_threadpool(close_clients)
        await run_in_threadpool(stop_runtime, telemetry)
        logger.info("admin_api_stopped")


def create_app() -> FastAPI:
    settings = core_settings()
    get_settings()  # Validate admin-only settings, without contacting the provider.
    configure_logging("admin-api", settings.log_level, settings.log_format, non_blocking=True)
    app = FastAPI(
        title="DevFeed Admin API",
        version=__version__,
        lifespan=lifespan,
        responses=ERROR_RESPONSES,
        description="Private administration API. OIDC sessions and CSRF protection required.",
    )
    app.state.codex = CodexConnection(settings)
    app.state.overview_panel_snapshots = {}
    app.state.overview_panel_tasks = {}
    app.state.overview_panel_slots = asyncio.Semaphore(2)
    app.state.overview_snapshots = {}
    app.state.overview_tasks = {}
    app.state.overview_retry_at = {}
    # No cross-origin cookie access: the Next.js admin service proxies same-origin requests.
    app.add_middleware(
        RequestLoggingMiddleware,
        service="admin-api",
        logger=logger,
        response_headers=((b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer")),
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
        try:
            revision = database_revision(session)
            session.close()
            if revision != SCHEMA_REVISION and revision not in BACKWARD_COMPATIBLE_SCHEMA_REVISIONS:
                return JSONResponse(
                    UnhealthyResponse(status="migration_required").model_dump(), status_code=503
                )
            get_redis().ping()
        except (SQLAlchemyError, RedisError):
            return JSONResponse(
                UnhealthyResponse(status="unavailable").model_dump(), status_code=503
            )
        return {"status": "ok"}

    for router in (
        auth.router,
        user_settings.router,
        users.router,
        ai_connection.router,
        overview.router,
        overview_panels.router,
        automation.router,
        taxonomy.router,
        topic_proposals.router,
        topic_relationships.router,
        topic_replacements.router,
        ingestion.router,
        sources.router,
        source_imports.router,
        articles.router,
        topics.router,
        jobs.router,
        notifications.router,
        workers.router,
    ):
        app.include_router(router)
    app.add_middleware(TelemetryMiddleware)
    return app


app = create_app()
