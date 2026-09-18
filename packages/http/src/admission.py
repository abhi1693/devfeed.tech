"""Bound HTTP work independently of database connection reuse.

There is no admission queue: overload is rejected before opening a DB connection
or occupying a sync worker thread. Notification streams have their own budget so
long-lived inbox connections cannot crowd out interactive requests.
"""

from devfeed_core.telemetry import current
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

STREAM_PATHS = frozenset(
    {
        "/v1/user/notifications/chimely/v1/inbox/stream",
        "/v1/admin/notifications/chimely/v1/inbox/stream",
    }
)


class AdmissionMiddleware:
    def __init__(self, app: ASGIApp, *, requests: int, streams: int):
        self.app = app
        self.limits = {"request": requests, "stream": streams}
        self.active = {"request": 0, "stream": 0}

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope.get("path") == "/health/live":
            await self.app(scope, receive, send)
            return
        kind = (
            "stream"
            if scope.get("method") == "GET" and scope.get("path") in STREAM_PATHS
            else "request"
        )
        # No await between the check and increment: atomic on the ASGI event loop.
        if self.active[kind] >= self.limits[kind]:
            if runtime := current():
                runtime.metrics.admission_rejections.labels(runtime.service, kind).inc()
            await JSONResponse(
                {"detail": "Service busy. Please retry shortly."},
                status_code=503,
                headers={"Retry-After": "1", "Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        self.active[kind] += 1
        try:
            await self.app(scope, receive, send)
        finally:
            # Keep the slot through response/background cleanup, including failures.
            self.active[kind] -= 1
