"""Process lifecycle and HTTP policy shared by independently deployed APIs."""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from devfeed_core.config import Settings
from devfeed_core.telemetry import start_runtime, stop_runtime
from devfeed_core.version import SCHEMA_REVISION
from fastapi import FastAPI
from fastapi.params import Depends
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from devfeed_http.admission import AdmissionMiddleware
from devfeed_http.errors import register_error_handlers
from devfeed_http.health import readiness_response
from devfeed_http.logging import RequestLoggingMiddleware
from devfeed_http.schemas import HealthResponse, UnhealthyResponse


@dataclass(frozen=True)
class HTTPService:
    """Compose process infrastructure without inheriting account or admin policy.

    Each app supplies its own clients, dependencies and optional resources.
    Cleanup runs in reverse acquisition order, including after startup failures.
    """

    name: str
    logger: logging.Logger
    close_clients: Callable[[], None]
    resources: Callable[[FastAPI], AbstractAsyncContextManager] | None = None

    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        runtime = start_runtime(self.name)
        async with AsyncExitStack() as stack:
            event_prefix = self.name.replace("-", "_")
            stack.callback(self.logger.info, event_prefix + "_stopped")
            stack.push_async_callback(run_in_threadpool, stop_runtime, runtime)
            stack.push_async_callback(run_in_threadpool, self.close_clients)
            self.logger.info(event_prefix + "_started")
            if self.resources is not None:
                await stack.enter_async_context(self.resources(app))
            yield

    def configure(
        self,
        app: FastAPI,
        settings: Settings,
        *,
        private: bool = False,
        middleware: Callable[[FastAPI], None] | None = None,
    ) -> None:
        app.add_middleware(
            AdmissionMiddleware,
            requests=settings.api_max_concurrent_requests,
            streams=settings.api_max_concurrent_streams,
        )
        if middleware is not None:
            middleware(app)
        app.add_middleware(
            RequestLoggingMiddleware,
            service=self.name,
            logger=self.logger,
            response_headers=(
                ((b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer"))
                if private
                else ()
            ),
        )
        register_error_handlers(app, self.logger, admin=private)

    def register_health(
        self,
        app: FastAPI,
        *,
        session_dependency: Depends,
        get_redis: Callable,
        revision_reader: Callable,
        schema_revision: str = SCHEMA_REVISION,
    ) -> None:
        @app.get("/health/live", tags=["health"], response_model=HealthResponse)
        async def live():
            return {"status": "ok"}

        @app.get(
            "/health/ready",
            tags=["health"],
            response_model=HealthResponse,
            responses={503: {"model": UnhealthyResponse, "description": "Not ready"}},
        )
        def ready(session: Annotated[Session, session_dependency]):
            return readiness_response(
                session,
                get_redis(),
                self.logger,
                revision_reader=revision_reader,
                schema_revision=schema_revision,
            )
