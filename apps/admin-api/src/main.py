"""Independently deployable admin API; public traffic never loads this service."""

import logging
from contextlib import asynccontextmanager

from devfeed_core.cache import close_cache
from devfeed_core.config import get_settings as core_settings
from devfeed_core.db import get_engine
from devfeed_core.logging import configure_logging
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.version import SCHEMA_REVISION, __version__
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

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
    workers,
)
from devfeed_admin_api.codex_connection import CodexConnection
from devfeed_admin_api.config import get_settings
from devfeed_admin_api.dependencies import DB, get_redis
from devfeed_admin_api.logging import RequestLoggingMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    logger.info("admin_api_started")
    app.state.codex.start()
    try:
        yield
    finally:
        await app.state.codex.close()
        close_cache()
        if get_engine.cache_info().currsize:
            get_engine().dispose()
        if get_redis.cache_info().currsize:
            get_redis().close()
        get_redis.cache_clear()
        logger.info("admin_api_stopped")


def create_app() -> FastAPI:
    settings = core_settings()
    get_settings()  # Validate admin-only settings, without contacting the provider.
    configure_logging("admin-api", settings.log_level, settings.log_format)
    app = FastAPI(
        title="DevFeed Admin API",
        version=__version__,
        lifespan=lifespan,
        description="Private administration API. OIDC sessions and CSRF protection required.",
    )
    app.state.codex = CodexConnection(settings)
    # No cross-origin cookie access: the Next.js admin service proxies same-origin requests.
    app.add_middleware(RequestLoggingMiddleware)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        return JSONResponse(
            {"detail": "Internal server error"},
            status_code=500,
            headers={"Cache-Control": "no-store", "X-Request-ID": request.state.request_id},
        )

    @app.exception_handler(IntegrityError)
    async def conflict(request: Request, exc: IntegrityError):
        return JSONResponse({"detail": "Conflicting or invalid record"}, status_code=409)

    @app.exception_handler(SQLAlchemyError)
    async def unavailable(request: Request, exc: SQLAlchemyError):
        logger.error("admin_database_unavailable", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            {"detail": "Database unavailable or migrations required"}, status_code=503
        )

    @app.exception_handler(RecordNotFound)
    async def missing(request: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(OperationConflict)
    async def invalid(request: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        return JSONResponse(
            {
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()
                ]
            },
            status_code=422,
        )

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready(session: DB):
        try:
            revision = session.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != SCHEMA_REVISION:
                return JSONResponse({"status": "migration_required"}, status_code=503)
            get_redis().ping()
        except (SQLAlchemyError, RedisError):
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ok"}

    for router in (
        auth.router,
        user_settings.router,
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
