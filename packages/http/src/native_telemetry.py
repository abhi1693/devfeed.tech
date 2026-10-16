"""Isolate FastAPI's native telemetry bridge at the already selected route boundary.

FastAPI starts HTTP telemetry before routing. This bridge defers it without
matching routes again or copying the SDK's span, metric and operation machinery.
"""

import asyncio
from contextlib import suppress
from typing import cast

from anyio import CancelScope
from fastapi import FastAPI
from fastapi.routing import _get_scope_effective_route_context
from fastapi.telemetry._api import _route_selected
from fastapi.telemetry._asgi import ExceptionTelemetryMiddleware, NativeTelemetry
from starlette._exception_handler import wrap_app_handling_exceptions
from starlette._utils import is_async_callable
from starlette.concurrency import run_in_threadpool
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute
from starlette.types import ASGIApp, HTTPExceptionHandler, Message, Receive, Scope, Send

from devfeed_http.route_policies import (
    CURRENT_ROUTE_SCOPE,
    DEFAULT_RUNTIME_POLICY,
    MOUNT_TEMPLATES_SCOPE_KEY,
    RUNTIME_POLICY_SCOPE_KEY,
)


class _ErrorResponseHandler:
    """Keep native response/rethrow behavior without invoking a handler twice."""

    def __init__(self, handler: HTTPExceptionHandler):
        self.handler = handler

    async def __call__(self, request: Request, exc: Exception) -> Response:
        responses = request.scope.setdefault("devfeed.runtime.error_responses", {})
        previous = responses.get(self)
        if previous is not None and previous[0] is exc:
            if isinstance(previous[1], Exception):
                raise previous[1]
            return cast(Response, previous[1])
        try:
            if is_async_callable(self.handler):
                response = await self.handler(request, exc)
            else:
                response = await run_in_threadpool(self.handler, request, exc)
        except Exception as handler_error:
            responses[self] = (handler_error, handler_error)
            raise
        responses[self] = (exc, response)
        return cast(Response, response)


class NativeRouteTelemetry:
    def __init__(self, app: FastAPI):
        self.app = app
        # Reuse the app's complete native configuration, including provider ownership.
        self.native = NativeTelemetry(app._telemetry)
        # Match the framework's last-registered 500/Exception callback. The outer
        # native error middleware sees the same exception after our observer ends.
        handlers = {
            key: value for key, value in app.exception_handlers.items() if key in (500, Exception)
        }
        self.error_handler = (
            _ErrorResponseHandler(next(reversed(handlers.values()))) if handlers else None
        )
        if self.error_handler is not None:
            for key in handlers:
                app.exception_handlers[key] = self.error_handler

    @staticmethod
    def route_template(scope: Scope, route: BaseRoute) -> str | None:
        context = _get_scope_effective_route_context(scope)
        template = (
            getattr(context.starlette_route, "path_format", None) or context.path_format
            if context is not None and context.original_route is route
            else getattr(route, "path_format", None)
        )
        return template if isinstance(template, str) else None

    async def run(
        self,
        app: ASGIApp,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        route: BaseRoute | None = None,
    ) -> None:
        # A parent observer owns the request. Exclusion markers from pre-routing
        # deferral are removed only here, after the actual policy has been bound.
        if scope.get("fastapi.telemetry") is not None:
            await app(scope, receive, send)
            return
        scope.pop("fastapi.telemetry", None)

        async def selected(scope: Scope, receive: Receive, send: Send) -> None:
            for template in scope.get(MOUNT_TEMPLATES_SCOPE_KEY, ()):
                _route_selected(scope=scope, path=template, mount=True)
            if route is not None:
                _route_selected(scope=scope, path=self.route_template(scope, route))
            telemetry = scope.get("fastapi.telemetry")
            span = getattr(telemetry, "span", None)
            if span is not None and (context := span.get_span_context()).is_valid:
                scope.setdefault("state", {})["request_trace"] = {
                    "trace_id": format(context.trace_id, "032x"),
                    "span_id": format(context.span_id, "016x"),
                }
            handled = wrap_app_handling_exceptions(app, Request(scope, receive))
            # Translate early405/404 and unhandled500 inside the observer too.
            # Existing outer handlers still receive the propagated exception.
            await ServerErrorMiddleware(
                ExceptionTelemetryMiddleware(handled),
                handler=self.error_handler,
                debug=self.app.debug,
            )(scope, receive, send)

        await self.native(app=selected, scope=scope, receive=receive, send=send)


class _ResponseObservation:
    """Observe pre-routing responses with one frame of backpressure and no body buffer."""

    def __init__(self, telemetry: NativeRouteTelemetry, scope: Scope, receive: Receive, send: Send):
        self.telemetry, self.scope, self.receive, self.send = telemetry, scope, receive, send
        self.frames: asyncio.Queue[tuple[Message, asyncio.Future[None]]] = asyncio.Queue(maxsize=1)
        self.task: asyncio.Task[None] | None = None

    async def relay(self, scope: Scope, receive: Receive, send: Send) -> None:
        trailers = False
        while True:
            message, acknowledged = await self.frames.get()
            try:
                if message["type"] == "http.response.start":
                    trailers = message.get("trailers", False)
                await send(message)
            except BaseException as exc:
                if not acknowledged.done():
                    acknowledged.set_exception(exc)
                raise
            else:
                if not acknowledged.done():
                    acknowledged.set_result(None)
            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and not trailers
                or message["type"] == "http.response.trailers"
                and not message.get("more_trailers", False)
                or message["type"] == "http.response.pathsend"
            ):
                return

    async def observe(self) -> None:
        token = CURRENT_ROUTE_SCOPE.set(self.scope)
        try:
            await self.telemetry.run(self.relay, self.scope, self.receive, self.send)
        finally:
            CURRENT_ROUTE_SCOPE.reset(token)

    async def send_frame(self, message: Message) -> None:
        if self.task is None:
            self.scope[RUNTIME_POLICY_SCOPE_KEY] = DEFAULT_RUNTIME_POLICY
            self.task = asyncio.create_task(self.observe())
        acknowledged: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        try:
            await self.frames.put((message, acknowledged))
            done, _ = await asyncio.wait(
                (acknowledged, self.task), return_when=asyncio.FIRST_COMPLETED
            )
            if acknowledged in done:
                await acknowledged
            else:
                await self.task
                raise RuntimeError("Response observer ended before acknowledging its frame")
        finally:
            # Cancellation can leave the relay holding this acknowledgement. Do
            # not let a later send failure create an unobserved future exception.
            if not acknowledged.done():
                acknowledged.cancel()
            elif not acknowledged.cancelled():
                acknowledged.exception()

    async def close(self) -> None:
        if self.task is not None:
            if not self.task.done():
                self.task.cancel()
            with CancelScope(shield=True), suppress(asyncio.CancelledError):
                await self.task


class RuntimePolicyMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("devfeed.runtime.observing"):
            await self.app(scope, receive, send)
            return
        scope["devfeed.runtime.observing"] = True
        binding = scope["app"].state.runtime_policy_binding
        observation = _ResponseObservation(binding.telemetry, scope, receive, send)

        async def observed_send(message: Message) -> None:
            if observation.task is not None or RUNTIME_POLICY_SCOPE_KEY not in scope:
                await observation.send_frame(message)
            else:
                await send(message)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            await observation.close()
