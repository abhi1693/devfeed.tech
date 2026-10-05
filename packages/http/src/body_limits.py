"""Bound selected request bodies before JSON decoding or database dependencies."""

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


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, limits: dict[tuple[str, str], int], timeout: float = 2.0):
        self.app = app
        self.limits = limits
        self.timeout = timeout

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        limit = self.limits.get((scope.get("method", ""), _route_path(scope)))
        if scope["type"] != "http" or limit is None:
            await self.app(scope, receive, send)
            return
        try:
            if _declared_oversize(scope, limit):
                raise RequestBodyTooLarge
            body = await _read_body(receive, limit, self.timeout)
        except (RequestBodyTooLarge, TimeoutError) as exc:
            too_large = isinstance(exc, RequestBodyTooLarge)
            await JSONResponse(
                {"detail": "Request too large" if too_large else "Incomplete request"},
                status_code=413 if too_large else 408,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
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
