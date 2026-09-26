import logging
from contextlib import asynccontextmanager

from devfeed_core.client_lifecycle import close_shared_clients
from devfeed_core.config import get_settings
from devfeed_core.db import database_revision
from devfeed_core.feeds.validation import FeedValidationError
from devfeed_core.logging import configure_logging
from devfeed_core.telemetry import start_runtime, stop_runtime
from devfeed_core.version import SCHEMA_REVISION, __version__
from devfeed_http.admission import AdmissionMiddleware
from devfeed_http.errors import register_error_handlers
from devfeed_http.health import readiness_response
from devfeed_http.logging import RequestLoggingMiddleware
from devfeed_http.schemas import (
    ERROR_RESPONSES,
    ErrorResponse,
    FeedValidationResponse,
    HealthResponse,
    UnhealthyResponse,
    VersionResponse,
)
from devfeed_http.telemetry import TelemetryMiddleware
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from devfeed_api import feed, search, sitemaps, sources, taxonomy, topics
from devfeed_api.dependencies import DB, get_redis

logger = logging.getLogger(__name__)


def close_clients():
    close_shared_clients(get_redis)


@asynccontextmanager
async def lifespan(app):
    telemetry = start_runtime("api")
    settings = get_settings()
    configure_logging("api", settings.log_level, settings.log_format, non_blocking=True)
    logger.info("api_started")
    try:
        yield
    finally:
        await run_in_threadpool(close_clients)
        await run_in_threadpool(stop_runtime, telemetry)
        logger.info("api_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging("api", settings.log_level, settings.log_format, non_blocking=True)
    app = FastAPI(
        title="DevFeed API",
        version=__version__,
        lifespan=lifespan,
        responses={**ERROR_RESPONSES, 422: {"model": ErrorResponse | FeedValidationResponse}},
        description="Developer article ingestion, taxonomy and discovery. No account layer.",
    )
    app.add_middleware(
        AdmissionMiddleware,
        requests=settings.api_max_concurrent_requests,
        streams=settings.api_max_concurrent_streams,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )

    app.add_middleware(RequestLoggingMiddleware, service="api", logger=logger)

    app.add_middleware(TelemetryMiddleware)

    register_error_handlers(app, logger)

    @app.exception_handler(FeedValidationError)
    async def invalid_feed(request: Request, exc: FeedValidationError):
        return JSONResponse(
            status_code=422,
            content=FeedValidationResponse(
                detail=str(exc), upstream_status=exc.upstream_status, retryable=exc.retryable
            ).model_dump(),
        )

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
            logger,
            revision_reader=database_revision,
            schema_revision=SCHEMA_REVISION,
        )

    @app.get("/version", tags=["operations"], response_model=VersionResponse)
    def app_version(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return VersionResponse(version=__version__, required_schema_revision=SCHEMA_REVISION)

    app.include_router(search.router)
    app.include_router(sitemaps.router)
    app.include_router(feed.router)
    app.include_router(sources.router)
    app.include_router(taxonomy.router)
    app.include_router(topics.router)
    return app


app = create_app()
