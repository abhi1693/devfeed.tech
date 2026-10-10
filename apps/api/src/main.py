import logging

from devfeed_core.client_lifecycle import close_shared_clients
from devfeed_core.config import get_settings
from devfeed_core.db import database_revision
from devfeed_core.feeds.validation import FeedValidationError
from devfeed_core.logging import configure_logging
from devfeed_core.version import SCHEMA_REVISION, __version__
from devfeed_http.body_limits import RequestBodyLimitMiddleware
from devfeed_http.schemas import (
    ERROR_RESPONSES,
    ErrorResponse,
    FeedValidationResponse,
    VersionResponse,
)
from devfeed_http.service import HTTPService
from devfeed_http.telemetry import fastapi_telemetry
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from devfeed_api import feed, partner_tracking, search, sitemaps, sources, taxonomy, topics
from devfeed_api.dependencies import database_session, get_rate_limit_redis, get_redis

logger = logging.getLogger(__name__)


def close_clients():
    close_shared_clients(get_redis, get_rate_limit_redis)


service = HTTPService("api", logger, lambda: close_clients())
lifespan = service.lifespan


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging("api", settings.log_level, settings.log_format, non_blocking=True)
    app = FastAPI(
        title="DevFeed API",
        version=__version__,
        lifespan=lifespan,
        telemetry=fastapi_telemetry(),
        responses={**ERROR_RESPONSES, 422: {"model": ErrorResponse | FeedValidationResponse}},
        description="Developer article ingestion, taxonomy and discovery. No account layer.",
    )

    def cors(app):
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH"],
            allow_headers=["Content-Type"],
            allow_credentials=False,
        )

    app.add_middleware(
        RequestBodyLimitMiddleware,
        limits={
            ("POST", "/v1/search/analytics/click"): 4096,
            ("POST", "/v1/partner-tracking/events"): 2048,
        },
    )
    service.configure(app, settings, middleware=cors)

    @app.exception_handler(FeedValidationError)
    async def invalid_feed(request: Request, exc: FeedValidationError):
        return JSONResponse(
            status_code=422,
            content=FeedValidationResponse(
                detail=str(exc), upstream_status=exc.upstream_status, retryable=exc.retryable
            ).model_dump(),
        )

    service.register_health(
        app,
        session_dependency=database_session,
        get_redis=lambda: get_redis(),
        revision_reader=lambda session: database_revision(session),
        schema_revision=SCHEMA_REVISION,
    )

    @app.get("/version", tags=["operations"], response_model=VersionResponse)
    def app_version(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return VersionResponse(version=__version__, required_schema_revision=SCHEMA_REVISION)

    app.include_router(partner_tracking.router)
    app.include_router(search.router)
    app.include_router(sitemaps.router)
    app.include_router(feed.router)
    app.include_router(sources.router)
    app.include_router(taxonomy.router)
    app.include_router(topics.router)
    return app


app = create_app()
