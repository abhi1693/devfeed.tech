"""Bound selected request bodies before parsing or database dependencies."""

from abc import ABC, abstractmethod
from collections.abc import Callable

import anyio
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class RequestBodyTooLarge(ValueError):
    pass


def _route_path(scope: Scope) -> str:
    path = scope.get("path", "")
    root = scope.get("root_path", "")
    return path[len(root) :] if root and path.startswith(root + "/") else path


def _declared_oversize(scope: Scope, limit: int) -> bool:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value) > limit
            except ValueError:
                pass  # The actual byte count remains authoritative.
    return False


async def _read_body(receive: Receive, limit: int, timeout: float) -> bytes | None:
    body = bytearray()
    with anyio.fail_after(timeout):
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                raise RequestBodyTooLarge
            body.extend(chunk)
            if not message.get("more_body", False):
                return bytes(body)


class BoundedRequestBodyMiddleware(ABC):
    """Read a limited body once, with route and response policy supplied by subclasses."""

    def __init__(self, app: ASGIApp, *, timeout: float):
        self.app, self.timeout = app, timeout

    @abstractmethod
    def body_limit(self, scope: Scope) -> int | None:
        """Return a byte limit, or leave this request untouched."""

    def declared_oversize(self, scope: Scope, limit: int) -> bool:
        return _declared_oversize(scope, limit)

    @abstractmethod
    async def reject(self, scope: Scope, receive: Receive, send: Send, status: int) -> None:
        """Send the route policy's size or timeout response."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.body_limit(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return
        try:
            if self.declared_oversize(scope, limit):
                raise RequestBodyTooLarge
            body = await _read_body(receive, limit, self.timeout)
        except (RequestBodyTooLarge, TimeoutError) as exc:
            await self.reject(
                scope, receive, send, 413 if isinstance(exc, RequestBodyTooLarge) else 408
            )
            return
        if body is None:
            return
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)


class RequestBodyLimitMiddleware(BoundedRequestBodyMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        limits: dict[tuple[str, str], int],
        timeout: float = 2.0,
        limit_for: Callable[[str, str], int | None] | None = None,
    ):
        super().__init__(app, timeout=timeout)
        self.limits, self.limit_for = limits, limit_for

    def body_limit(self, scope: Scope) -> int | None:
        method, path = scope.get("method", ""), _route_path(scope)
        limit = self.limits.get((method, path))
        if limit is None and self.limit_for is not None:
            limit = self.limit_for(method, path)
        return limit

    async def reject(self, scope: Scope, receive: Receive, send: Send, status: int) -> None:
        await JSONResponse(
            {"detail": "Request too large" if status == 413 else "Incomplete request"},
            status_code=status,
            headers={"Cache-Control": "no-store"},
        )(scope, receive, send)
