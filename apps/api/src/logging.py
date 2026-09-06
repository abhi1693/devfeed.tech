"""ASGI request logging keeps context alive through response bodies and failures."""

import logging
import time
import uuid

from devfeed_core.logging import elapsed_ms, log_context
from devfeed_core.version import __version__
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid.uuid4())  # Do not trust client-supplied correlation IDs.
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status = 500
        failed = False

        async def send_response(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                headers.append((b"x-devfeed-version", __version__.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        with log_context(service="api", request_id=request_id):
            try:
                await self.app(scope, receive, send_response)
            except Exception:
                failed = True
                logger.exception("request_failed", extra={"route": route_name(scope)})
                raise
            finally:
                route = route_name(scope)
                level = (
                    logging.ERROR
                    if failed or status >= 500
                    else (logging.WARNING if status >= 400 else logging.INFO)
                )
                if status < 400 and not failed and route in {"/health/live", "/health/ready"}:
                    level = logging.DEBUG
                logger.log(
                    level,
                    "request_completed",
                    extra={
                        "method": scope["method"],
                        "route": route,
                        "status_code": status,
                        "duration_ms": elapsed_ms(started),
                        "cache_status": scope.get("state", {}).get("cache_status"),
                        "cache_bypass_reason": scope.get("state", {}).get("cache_bypass_reason"),
                    },
                )


def route_name(scope: Scope) -> str:
    # Unmatched paths may themselves contain secrets; never fall back to the raw URL.
    route = scope.get("route")
    return getattr(route, "path", "<unmatched>")
