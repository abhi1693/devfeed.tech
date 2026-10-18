"""Bound registered HTTP operations independently of database connection reuse.

Routes claim their declared lane before dependencies or request bodies run. The
middleware owns each claim until response and background cleanup finish, so a
long-lived stream never consumes an interactive request slot.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any, Literal

from devfeed_core.telemetry import current
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ADMISSION_SCOPE_KEY = "devfeed.admission"
_CONTEXT_SCOPE_KEY = "devfeed.admission.context"
AdmissionLane = Literal["request", "stream", "exempt"]
BoundedLane = Literal["request", "stream"]


@dataclass
class _AdmissionLease:
    controller: "AdmissionController"
    lane: BoundedLane
    admitted: Any
    route: str = "unmatched"
    started: float = 0.0
    route_active: Any = None
    duration: Any = None
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        # Synchronous release also runs when the request task is cancelled.
        self.controller.active[self.lane] -= 1
        if self.admitted:
            self.admitted.add(-1, {"kind": self.lane})
        attributes = {"kind": self.lane, "http.route": self.route}
        if self.route_active:
            self.route_active.add(-1, attributes)
        if self.duration:
            self.duration.record(monotonic() - self.started, attributes)


@dataclass
class _AdmissionContext:
    controller: "AdmissionController"
    claimed: bool = False
    rejected: bool = False
    lease: _AdmissionLease | None = None

    def claim(self, lane: AdmissionLane, route: str = "unmatched") -> bool:
        if self.claimed:
            return not self.rejected
        self.claimed = True
        if lane != "exempt":
            self.lease = self.controller.acquire(lane, route=route)
            self.rejected = self.lease is None
        return not self.rejected

    def close(self) -> None:
        if self.lease is not None:
            self.lease.close()


class AdmissionController:
    """One process budget; route selection supplies the request's lane."""

    def __init__(self, *, requests: int, streams: int):
        self.limits = {"request": requests, "stream": streams}
        self.active = {"request": 0, "stream": 0}

    def acquire(self, lane: BoundedLane, *, route: str = "unmatched") -> _AdmissionLease | None:
        # No await between the check and increment: atomic on the ASGI event loop.
        if self.active[lane] >= self.limits[lane]:
            if (runtime := current()) and (instrument := runtime.instruments.get("rejections")):
                instrument.add(1, {"kind": lane})
            return None
        self.active[lane] += 1
        runtime = current()
        admitted = runtime.instruments.get("admitted") if runtime else None
        if admitted and runtime:
            admitted.add(1, {"kind": lane})
            runtime.instruments["limit"].set(self.limits[lane], {"kind": lane})
        route_active = runtime.instruments.get("admission_route_active") if runtime else None
        duration = runtime.instruments.get("admission_duration") if runtime else None
        if route_active:
            route_active.add(1, {"kind": lane, "http.route": route})
        return _AdmissionLease(self, lane, admitted, route, monotonic(), route_active, duration)

    @staticmethod
    def busy_response(headers: Sequence[tuple[bytes, bytes]] = ()) -> JSONResponse:
        response = JSONResponse(
            {"detail": "Service busy. Please retry shortly."},
            status_code=503,
            headers={"Retry-After": "1", "Cache-Control": "no-store"},
        )
        # A preflight can complete before routing. Preserve only the CORS policy
        # already emitted by the framework when replacing that response.
        response.raw_headers.extend(
            (name, value)
            for name, value in headers
            if name.lower().startswith(b"access-control-") or name.lower() == b"vary"
        )
        return response

    async def run(
        self,
        lane: AdmissionLane,
        scope: Scope,
        receive: Receive,
        send: Send,
        app: ASGIApp,
    ) -> None:
        previous = scope.get(_CONTEXT_SCOPE_KEY)
        if isinstance(previous, _AdmissionContext) and previous.controller is self:
            context = previous
            owns_context = False
        else:
            context = _AdmissionContext(self)
            scope[_CONTEXT_SCOPE_KEY] = context
            owns_context = True
        try:
            if context.claim(lane, route=getattr(scope.get("route"), "path", "unmatched")):
                await app(scope, receive, send)
            else:
                await self.busy_response()(scope, receive, send)
        finally:
            # A middleware-owned lease includes outer exception translation and
            # background response work; a direct call owns its own cleanup.
            if owns_context:
                context.close()
                if previous is None:
                    scope.pop(_CONTEXT_SCOPE_KEY, None)
                else:
                    scope[_CONTEXT_SCOPE_KEY] = previous


class AdmissionMiddleware:
    def __init__(self, app: ASGIApp, *, requests: int, streams: int):
        self.app = app
        self.controller = AdmissionController(requests=requests, streams=streams)

    @property
    def active(self) -> dict[str, int]:
        return self.controller.active

    @property
    def limits(self) -> dict[str, int]:
        return self.controller.limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Mounted apps share the outer request's budget and cleanup owner.
        if isinstance(scope.get(ADMISSION_SCOPE_KEY), AdmissionController):
            await self.app(scope, receive, send)
            return

        context = _AdmissionContext(self.controller)
        scope[ADMISSION_SCOPE_KEY] = self.controller
        scope[_CONTEXT_SCOPE_KEY] = context
        fallback_rejected = False

        async def guarded_send(message: Message) -> None:
            nonlocal fallback_rejected
            if fallback_rejected:
                return
            if (
                message["type"] == "http.response.start"
                and not context.claimed
                and not context.claim("request")
            ):
                fallback_rejected = True
                await self.controller.busy_response(message.get("headers", []))(
                    scope, receive, send
                )
                return
            await send(message)

        try:
            await self.app(scope, receive, guarded_send)
        finally:
            context.close()
