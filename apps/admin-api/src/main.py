"""Independently deployable admin API; public traffic never loads this service."""

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager

from devfeed_core.client_lifecycle import close_shared_clients
from devfeed_core.config import get_settings as core_settings
from devfeed_core.db import database_revision
from devfeed_core.logging import configure_logging
from devfeed_core.version import SCHEMA_REVISION, __version__
from devfeed_http.schemas import ERROR_RESPONSES
from devfeed_http.service import HTTPService
from devfeed_http.telemetry import fastapi_telemetry
from fastapi import FastAPI

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
    push_analytics,
    search_analytics,
    search_suggestions,
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
from devfeed_admin_api.dependencies import database_session, get_redis
from devfeed_admin_api.reporting import close_reporting

logger = logging.getLogger(__name__)


def close_clients():
    try:
        close_reporting()
    finally:
        close_shared_clients(get_redis)


@asynccontextmanager
async def admin_resources(app):
    async with AsyncExitStack() as stack:
        stack.push_async_callback(app.state.codex.close)
        app.state.codex.start()
        stack.push_async_callback(overview_panels.close_panel_tasks, app)
        stack.push_async_callback(overview.close_snapshot_tasks, app)
        yield


service = HTTPService("admin-api", logger, lambda: close_clients(), admin_resources)
lifespan = service.lifespan


def create_app() -> FastAPI:
    settings = core_settings()
    get_settings()  # Validate admin-only settings, without contacting the provider.
    configure_logging("admin-api", settings.log_level, settings.log_format, non_blocking=True)
    app = FastAPI(
        title="DevFeed Admin API",
        version=__version__,
        lifespan=lifespan,
        telemetry=fastapi_telemetry(),
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
    service.configure(app, settings, private=True)

    service.register_health(
        app,
        session_dependency=database_session,
        get_redis=lambda: get_redis(),
        revision_reader=lambda session: database_revision(session),
        schema_revision=SCHEMA_REVISION,
    )

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
        search_suggestions.router,
        search_analytics.router,
        push_analytics.router,
        articles.router,
        topics.router,
        jobs.router,
        notifications.router,
        workers.router,
    ):
        app.include_router(router)
    return app


app = create_app()
