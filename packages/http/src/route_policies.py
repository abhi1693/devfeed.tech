"""Shared runtime behavior belongs to selected operations, not URL spellings."""

from contextvars import ContextVar
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from pydantic import BaseModel, ConfigDict
from starlette.routing import BaseRoute, Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from devfeed_http.admission import ADMISSION_SCOPE_KEY, AdmissionController

RUNTIME_POLICY_EXTENSION = "x-devfeed-runtime"
RUNTIME_POLICY_SCOPE_KEY = "devfeed.runtime.policy"
MOUNT_TEMPLATES_SCOPE_KEY = "devfeed.runtime.mounts"
CURRENT_ROUTE_SCOPE: ContextVar[Scope | None] = ContextVar("devfeed_route_scope", default=None)


class RouteRuntimePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    admission: Literal["request", "stream", "exempt"] = "request"
    telemetry_excluded: bool = False
    logging_level: Literal["info", "debug"] = "info"

    def openapi_extra(self) -> dict[str, Any]:
        return {RUNTIME_POLICY_EXTENSION: self.model_dump()}

    @classmethod
    def from_scope(cls, scope: Scope) -> "RouteRuntimePolicy":
        policy = scope.get(RUNTIME_POLICY_SCOPE_KEY)
        return policy if isinstance(policy, cls) else DEFAULT_RUNTIME_POLICY


DEFAULT_RUNTIME_POLICY = RouteRuntimePolicy()


class _RouteBoundary:
    def __init__(self, handle: ASGIApp, route: BaseRoute | None = None) -> None:
        self.handle, self.route = handle, route
        extra = getattr(route, "openapi_extra", None) or {}
        self.policy = RouteRuntimePolicy.model_validate(extra.get(RUNTIME_POLICY_EXTENSION, {}))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.handle(scope, receive, send)
            return
        methods = getattr(self.route, "methods", None)
        # A native method mismatch is an ordinary request, not the declared operation.
        policy = (
            DEFAULT_RUNTIME_POLICY if methods and scope["method"] not in methods else self.policy
        )
        scope[RUNTIME_POLICY_SCOPE_KEY] = policy
        token = CURRENT_ROUTE_SCOPE.set(scope)

        async def admitted(scope: Scope, receive: Receive, send: Send) -> None:
            controller = scope.get(ADMISSION_SCOPE_KEY)
            if isinstance(controller, AdmissionController):
                await controller.run(policy.admission, scope, receive, send, self.handle)
            else:
                await self.handle(scope, receive, send)

        try:
            state = getattr(scope.get("app"), "state", None)
            binding = getattr(state, "runtime_policy_binding", None)
            if binding is not None:
                await binding.telemetry.run(admitted, scope, receive, send, route=self.route)
            else:
                await admitted(scope, receive, send)
        finally:
            CURRENT_ROUTE_SCOPE.reset(token)


class _MountBoundary:
    def __init__(self, handle: ASGIApp, route: Mount) -> None:
        self.handle, self.route = handle, route

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        from devfeed_http.native_telemetry import NativeRouteTelemetry

        templates = list(scope.get(MOUNT_TEMPLATES_SCOPE_KEY, ()))
        template = NativeRouteTelemetry.route_template(scope, self.route)
        if template is not None:
            templates.append(template)
        scope[MOUNT_TEMPLATES_SCOPE_KEY] = templates
        await self.handle(scope, receive, send)


class RuntimePolicyBinding:
    """Decorate native route objects once; the framework still selects routes."""

    def __init__(self, app: FastAPI) -> None:
        from devfeed_http.native_telemetry import NativeRouteTelemetry

        self.app = app
        self.telemetry = NativeRouteTelemetry(app)

    def install(self) -> None:
        for context in iter_route_contexts(self.app.routes):
            # Native deferred includes dispatch Starlette routes through their
            # effective copies; APIRoute handles remain on the original object.
            # FastAPI's RouteContext wraps that dispatch object separately from
            # its original route and the metadata used for API route includes.
            effective = context._effective_route
            route = effective if isinstance(effective, BaseRoute) else context.original_route
            if isinstance(route, Mount):
                child = route.app
                while (
                    not isinstance(child, FastAPI)
                    and (wrapped := getattr(child, "app", None)) is not None
                ):
                    child = wrapped
                if isinstance(child, FastAPI):
                    if not isinstance(route.handle, _MountBoundary):
                        route.handle = _MountBoundary(  # type: ignore[method-assign]
                            route.handle, route
                        )
                    install_runtime_policies(child)
                    continue
            if not isinstance(route.handle, _RouteBoundary):
                route.handle = _RouteBoundary(  # type: ignore[method-assign]
                    route.handle, context.original_route
                )
        if not isinstance(self.app.router.default, _RouteBoundary):
            self.app.router.default = _RouteBoundary(self.app.router.default)


def install_runtime_policies(app: FastAPI) -> None:
    """Run after registration; repeat to bind routes added by tests or extensions."""
    from devfeed_http.native_telemetry import RuntimePolicyMiddleware

    binding = getattr(app.state, "runtime_policy_binding", None)
    if binding is None:
        binding = RuntimePolicyBinding(app)
        app.state.runtime_policy_binding = binding
        app.add_middleware(RuntimePolicyMiddleware)
    binding.install()
