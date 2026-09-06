import logging
from contextlib import asynccontextmanager

from devfeed_core.cache import close_cache
from devfeed_core.categories import CategoryNotFound, InvalidCategoryTree
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.feeds.validation import FeedValidationError
from devfeed_core.logging import configure_logging
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.version import SCHEMA_REVISION, __version__
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from devfeed_api import feed, ingestion, sources, taxonomy, topics
from devfeed_api.dependencies import DB, get_redis
from devfeed_api.logging import RequestLoggingMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    configure_logging("api", settings.log_level, settings.log_format)
    logger.info("api_started")
    try:
        yield
    finally:
        close_cache()
        if get_engine.cache_info().currsize:
            get_engine().dispose()
        if get_redis.cache_info().currsize:
            get_redis().close()
        get_redis.cache_clear()
        logger.info("api_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging("api", settings.log_level, settings.log_format)
    app = FastAPI(
        title="DevFeed API",
        version=__version__,
        lifespan=lifespan,
        description="Developer article ingestion, taxonomy and discovery. No account layer.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )

    app.add_middleware(RequestLoggingMiddleware)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        # The middleware logs the safe stack. ServerErrorMiddleware sends this response
        # outside that middleware, so attach the same request ID explicitly on 500s.
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
            headers={"X-Request-ID": request.state.request_id},
        )

    @app.exception_handler(IntegrityError)
    async def conflict(request: Request, exc: IntegrityError):
        logger.warning("request_conflict", extra={"error_type": type(exc).__name__})
        return JSONResponse(status_code=409, content={"detail": "Conflicting or invalid record"})

    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable(request: Request, exc: SQLAlchemyError):
        logger.error("database_operation_failed", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Database unavailable or schema out of date. "
                    "Check connectivity and apply pending migrations."
                )
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(FeedValidationError)
    async def invalid_feed(request: Request, exc: FeedValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": str(exc),
                "upstream_status": exc.upstream_status,
                "retryable": exc.retryable,
            },
        )

    @app.exception_handler(CategoryNotFound)
    @app.exception_handler(RecordNotFound)
    async def unknown_category(request: Request, exc: CategoryNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(InvalidCategoryTree)
    @app.exception_handler(OperationConflict)
    async def invalid_tree(request: Request, exc: InvalidCategoryTree):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError):
        logger.warning("request_validation_failed", extra={"error_type": type(exc).__name__})
        # Keep validation responses bounded rather than echoing raw input.
        errors = [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready(session: DB):
        try:
            session.execute(text("SELECT 1"))
            revision = session.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != SCHEMA_REVISION:
                return JSONResponse(status_code=503, content={"status": "migration_required"})
            get_redis().ping()
        except (SQLAlchemyError, RedisError) as exc:
            logger.warning("readiness_failed", extra={"error_type": type(exc).__name__})
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ok"}

    @app.get("/version", tags=["operations"])
    def app_version():
        return JSONResponse(
            {"version": __version__, "required_schema_revision": SCHEMA_REVISION},
            headers={"Cache-Control": "no-store"},
        )

    app.include_router(feed.router)
    app.include_router(sources.router)
    app.include_router(taxonomy.router)
    app.include_router(topics.router)
    app.include_router(ingestion.router)
    return app


app = create_app()
