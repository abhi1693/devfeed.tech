"""Independently deployable admin API; public traffic never loads this service."""

import logging
from contextlib import asynccontextmanager

from devfeed_core.cache import close_cache
from devfeed_core.config import get_settings as core_settings
from devfeed_core.db import database_revision, get_engine
from devfeed_core.logging import configure_logging
from devfeed_core.version import SCHEMA_REVISION, __version__
from devfeed_http.errors import register_error_handlers
from devfeed_http.logging import RequestLoggingMiddleware
from devfeed_http.schemas import ERROR_RESPONSES, HealthResponse, UnhealthyResponse
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
    knowledge,
    notifications,
    overview,
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
    logger.info("admin_api_started")
    app.state.codex.start()
    try:
        yield
    finally:
        await app.state.codex.close()
        await run_in_threadpool(close_clients)
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
            if revision != SCHEMA_REVISION:
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
        automation.router,
        knowledge.router,
        taxonomy.router,
        topic_proposals.router,
        topic_relationships.router,
        topic_replacements.router,
        ingestion.router,
        sources.router,
        articles.router,
        topics.router,
        jobs.router,
        notifications.router,
        workers.router,
    ):
        app.include_router(router)
    return app


app = create_app()
