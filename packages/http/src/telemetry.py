"""HTTP RED metrics and trace context, without raw URLs or request bodies."""

import time

from devfeed_core.telemetry import current, extract_context, span
from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
EXCLUDED = frozenset({"/health/live", "/health/ready", "/version"})


class TelemetryMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        runtime = current()
        if scope["type"] != "http" or not runtime or scope.get("path") in EXCLUDED:
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "OTHER")
        method = method if method in METHODS else "OTHER"
        parent = next(
            (
                value.decode("ascii", "ignore")
                for key, value in scope.get("headers", [])
                if key == b"traceparent"
            ),
            "",
        )
        started = time.monotonic()
        status = 500
        metrics = runtime.metrics
        metrics.inflight.labels(runtime.service).inc()

        async def capture(message: Message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        with span(
            "HTTP " + method,
            context=extract_context({"traceparent": parent}),
            kind=trace.SpanKind.SERVER,
        ) as active:
            try:
                await self.app(scope, receive, capture)
            except BaseException:
                status = 500
                raise
            finally:
                # Starlette sets route only after matching. Never fall back to a
                # user-controlled path (including 404s), even for trace names.
                route = getattr(scope.get("route"), "path", "unmatched")
                active.update_name(method + " " + route)
                active.set_attributes(
                    {
                        "http.request.method": method,
                        "http.route": route,
                        "http.response.status_code": status,
                    }
                )
                if status >= 500:
                    active.set_status(trace.StatusCode.ERROR)
                metrics.requests.labels(runtime.service, method, route, str(status)).inc()
                metrics.request_duration.labels(runtime.service, method, route).observe(
                    time.monotonic() - started
                )
                metrics.inflight.labels(runtime.service).dec()
