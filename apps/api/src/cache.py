"""Opt-in route cache, before dependency resolution and database checkout."""

import time
from contextlib import suppress
from urllib.parse import urlencode

import anyio
from devfeed_core.cache import CacheUnavailable, get_cache
from devfeed_core.config import get_settings
from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

WAIT_SECONDS = 1.0
POLL_SECONDS = 0.05


def identity(request: Request) -> str:
    # Sort parameter names but preserve repeated values/order. Sorting all pairs
    # would alias ?limit=1&limit=2 with the reverse (different FastAPI semantics).
    query = urlencode(sorted(request.query_params.multi_items(), key=lambda pair: pair[0]))
    return request.url.path + "?" + query


def tagged(
    request: Request, response: Response, status: str, reason: str | None = None
) -> Response:
    request.state.cache_status = status
    request.state.cache_bypass_reason = reason
    response.headers["X-Cache"] = status
    if reason:
        response.headers["X-Cache-Bypass-Reason"] = reason
    # This is server-side caching only. Browsers/proxies must not independently
    # retain responses past a source review decision or another invalidation.
    response.headers["Cache-Control"] = "no-store"
    return response


class CachedReadRoute(APIRoute):
    """Shared, non-personalized reads only; cookie-dependent endpoints must not opt in."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def cached(request: Request) -> Response:
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
                return tagged(request, await handler(request), "BYPASS", reason)
            ttl = (
                settings.cache_metadata_ttl_seconds
                if self.path.startswith(("/v1/sources", "/v1/tags", "/v1/topics"))
                else settings.cache_ttl_seconds
            )
            cache = get_cache()
            key = identity(request)
            deadline = time.monotonic() + WAIT_SECONDS
            try:
                while True:
                    lookup = await run_in_threadpool(cache.lookup, key, "public")
                    if lookup.body is not None:
                        return tagged(
                            request, Response(lookup.body, media_type="application/json"), "HIT"
                        )
                    if lookup.token or time.monotonic() >= deadline:
                        break
                    await anyio.sleep(POLL_SECONDS)
            except CacheUnavailable:
                return tagged(request, await handler(request), "BYPASS", "cache_unavailable")
            try:
                response = await handler(request)
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
                return tagged(request, response, "MISS")
            finally:
                # Bounded, owner-checked release even on endpoint failure/cancellation.
                with anyio.CancelScope(shield=True), suppress(CacheUnavailable):
                    await run_in_threadpool(cache.release, lookup)

        return cached
