"""Opt-in route cache, before dependency resolution and database checkout."""

import json
import time
from collections.abc import Callable, Coroutine
from contextlib import suppress
from typing import Literal

import anyio
from devfeed_core.cache import CacheUnavailable, get_cache, record_cache_read
from devfeed_core.config import Settings, get_settings
from devfeed_core.json_types import JsonValue
from devfeed_http.body_limits import BodyLimitedRoute
from fastapi import Request, Response
from fastapi.dependencies.models import Dependant
from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

WAIT_SECONDS = 1.0
POLL_SECONDS = 0.05
CACHE_POLICY_EXTENSION = "x-devfeed-cache"


def query_identity_fields(dependant: Dependant) -> frozenset[str] | None:
    """Read native query aliases once; unknown model semantics stay conservative."""
    names: set[str] = set()
    pending, visited = [dependant], set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        pending.extend(current.dependencies)
        for field in current.query_params:
            annotation = field.field_info.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                # Query models can reject unknown keys; dropping those keys
                # could incorrectly turn a validation failure into a cache hit.
                return None
            names.update((field.alias, field.name))
            alias = field.field_info.validation_alias
            if isinstance(alias, str):
                names.add(alias)
            elif isinstance(alias, AliasChoices):
                if any(not isinstance(choice, str) for choice in alias.choices):
                    return None
                names.update(choice for choice in alias.choices if isinstance(choice, str))
            elif isinstance(alias, AliasPath):
                return None
    return frozenset(names)


class CachePolicy(BaseModel):
    """A public read owns its cache identity, schema revision and configured TTL."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    namespace: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    revision: int = Field(ge=1)
    ttl: Literal["content", "metadata"]
    normalize_query: bool = False

    def openapi_extra(self) -> dict[str, JsonValue]:
        return {CACHE_POLICY_EXTENSION: self.model_dump()}

    def identity(self, request: Request, query_names: frozenset[str] | None = None) -> str:
        # Keep repeated query values in their original order: FastAPI can select
        # different values for ?limit=1&limit=2 and its reverse. Structured fields
        # also keep detail IDs and query values from colliding through delimiters.
        return json.dumps(
            {
                "namespace": self.namespace,
                "revision": self.revision,
                "parameters": sorted(
                    (name, str(value)) for name, value in request.path_params.items()
                ),
                "query": sorted(
                    (
                        (name, value)
                        for name, value in request.query_params.multi_items()
                        if query_names is None or name in query_names
                    ),
                    key=lambda pair: pair[0],
                ),
            },
            separators=(",", ":"),
        )

    def ttl_seconds(self, settings: Settings) -> int:
        return (
            settings.cache_metadata_ttl_seconds
            if self.ttl == "metadata"
            else settings.cache_ttl_seconds
        )


def tagged(
    request: Request,
    response: Response,
    status: str,
    reason: str | None = None,
    *,
    route: str,
) -> Response:
    request.state.cache_status = status
    request.state.cache_bypass_reason = reason
    record_cache_read("public_response", status.lower(), reason, route=route)
    response.headers["X-Cache"] = status
    if reason:
        response.headers["X-Cache-Bypass-Reason"] = reason
    # This is server-side caching only. Browsers/proxies must not independently
    # retain responses past a source review decision or another invalidation.
    response.headers["Cache-Control"] = "no-store"
    return response


class CachedReadRoute(BodyLimitedRoute):
    """Shared, non-personalized reads only; cookie-dependent endpoints must not opt in."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[None, None, Response]]:
        handler = super().get_route_handler()
        extra = self.openapi_extra or {}
        policy = (
            CachePolicy.model_validate(extra[CACHE_POLICY_EXTENSION])
            if CACHE_POLICY_EXTENSION in extra
            else None
        )
        if policy is None:
            return handler
        query_names = query_identity_fields(self.dependant) if policy.normalize_query else None

        async def cached(request: Request) -> Response:
            async def render() -> Response:
                try:
                    return await handler(request)
                finally:
                    # FastAPI's function dependency stack closes outside this
                    # route wrapper. JSON serialization has finished here, so
                    # return the read connection before publishing to Redis.
                    session = getattr(request.state, "public_read_session", None)
                    if session is not None:
                        with anyio.CancelScope(shield=True):
                            await run_in_threadpool(session.close)

            if request.method != "GET":
                return await handler(request)
            settings = get_settings()
            controls = {
                part.strip().lower() for part in request.headers.get("cache-control", "").split(",")
            }
            # These endpoints do not consume cookies. Incidental browser cookies
            # must not disable shared caching (including requests from Swagger).
            reason = None
            if not settings.cache_enabled:
                reason = "disabled"
            elif "authorization" in request.headers:
                reason = "authorization"
            elif controls & {"no-cache", "no-store"}:
                reason = "request_cache_control"
            elif len(request.scope.get("query_string", b"")) > 4096:
                reason = "query_too_long"
            if reason:
                return tagged(request, await render(), "BYPASS", reason, route=self.path)
            ttl = policy.ttl_seconds(settings)
            cache = get_cache()
            key = policy.identity(request, query_names)
            deadline = time.monotonic() + WAIT_SECONDS
            try:
                while True:
                    lookup = await run_in_threadpool(
                        lambda: cache.lookup(key, "public", observe=False)
                    )
                    if lookup.body is not None:
                        return tagged(
                            request,
                            Response(lookup.body, media_type="application/json"),
                            "HIT",
                            route=self.path,
                        )
                    if lookup.token or time.monotonic() >= deadline:
                        break
                    await anyio.sleep(POLL_SECONDS)
            except CacheUnavailable:
                return tagged(
                    request, await render(), "BYPASS", "cache_unavailable", route=self.path
                )
            try:
                response = await render()
                body = getattr(response, "body", None)
                if (
                    response.status_code == 200
                    and isinstance(body, bytes)
                    and response.headers.get("content-type", "").startswith("application/json")
                    and "set-cookie" not in response.headers
                    and not response.headers.get("cache-control")
                    and not response.headers.get("vary")
                ):
                    with suppress(CacheUnavailable):
                        await run_in_threadpool(cache.publish, lookup, body, ttl)
                return tagged(request, response, "MISS", route=self.path)
            finally:
                # Bounded, owner-checked release even on endpoint failure/cancellation.
                with anyio.CancelScope(shield=True), suppress(CacheUnavailable):
                    await run_in_threadpool(cache.release, lookup)

        return cached
