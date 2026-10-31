"""Disposable Redis response cache; never owns database or RQ state."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Collection
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from typing import TypeVar, cast

from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from devfeed_core.config import get_settings
from devfeed_core.redis import create_redis
from devfeed_core.telemetry import current
from redis import Redis

logger = logging.getLogger(__name__)
LOCK_SECONDS = 30
RETRY_SECONDS = 5
CACHE_NAMES = frozenset(
    {
        "public_response",
        "public_profile",
        "admin_overview",
        "admin_panels",
        "admin_snapshot",
        "catalog",
        "feed_sequence",
    }
)
CACHE_REASONS = frozenset(
    {
        "disabled",
        "authorization",
        "request_cache_control",
        "query_too_long",
        "cache_unavailable",
        "invalid_entry",
        "stale",
        "none",
    }
)
PUBLIC_CACHE_ROUTES = frozenset(
    {
        "/v1/feed",
        "/v1/feed/options",
        "/v1/articles/{article_id}",
        "/v1/sources",
        "/v1/sources/{source_id}",
        "/v1/tags",
        "/v1/tags/{slug}",
        "/v1/topics",
        "/v1/topics/{slug}",
        "/v1/topics/{slug}/relations",
        "/v1/search",
        "/v1/search/suggestions",
    }
)
INVALIDATION_REASONS = frozenset(
    {
        "sources",
        "articles",
        "article_origins",
        "article_tags",
        "tags",
        "topics",
        "topic_relations",
        "article_topics",
        "profile",
        "avatar",
        "search_index",
        "manual",
        "unspecified",
        "other",
    }
)


def record_cache_read(
    name: str, outcome: str, reason: str | None = None, *, route: str | None = None
) -> None:
    if (runtime := current()) and (instrument := runtime.instruments.get("cache")):
        attributes = {
            "cache.name": name if name in CACHE_NAMES else "other",
            "cache.outcome": outcome if outcome in {"hit", "miss", "bypass"} else "other",
            "cache.bypass_reason": reason if reason in CACHE_REASONS else "none",
        }
        if name == "public_response" and route is not None:
            attributes["http.route"] = route if route in PUBLIC_CACHE_ROUTES else "other"
        instrument.add(1, attributes)


def record_cache_invalidation(domain: str, outcome: str, reasons: Collection[str]) -> None:
    if runtime := current():
        attributes = {
            "cache.domain": domain
            if domain in {"public", "operations", "search-index"}
            else "other",
            "cache.outcome": (
                outcome if outcome in {"success", "cache_unavailable", "disabled"} else "other"
            ),
        }
        if instrument := runtime.instruments.get("cache_invalidations"):
            instrument.add(1, attributes)
        if instrument := runtime.instruments.get("cache_invalidation_causes"):
            # One invalidation can have several causes. Deduplicate within the
            # transaction; never count this counter as the number of invalidations.
            bounded = {reason if reason in INVALIDATION_REASONS else "other" for reason in reasons}
            for reason in bounded or {"unspecified"}:
                instrument.add(1, {**attributes, "cache.invalidation_reason": reason})


# A late user may not repopulate a generation invalidated by a committed write,
# or replace another loader's response after losing its lock.
PUBLISH = """
local generations = #KEYS - 2
for i = 1, generations do
    if redis.call('GET', KEYS[i]) ~= ARGV[i] then return 0 end
end
if redis.call('GET', KEYS[generations + 1]) == ARGV[generations + 1] then
    redis.call('SET', KEYS[generations + 2], ARGV[generations + 2], 'EX', ARGV[generations + 3])
    return 1
end
return 0
"""

RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class CacheUnavailable(Exception):
    """The caller should use the database, not fail the request/write."""


@dataclass(frozen=True)
class CacheLookup:
    generation_key: str
    generation: bytes
    key: str
    lock_key: str
    token: str | None = None
    body: bytes | None = None
    dependencies: tuple[tuple[str, bytes], ...] = ()


T = TypeVar("T")


class ResponseCache:
    def __init__(self, redis: Redis, namespace: str) -> None:
        self.redis = redis
        self.namespace = namespace
        self.blocked_until = 0.0
        self.guard = threading.Lock()
        self.sitemap: ResponseCache | None = None
        self.feed_sequences: ResponseCache | None = None

    def _run(self, operation: Callable[[], T]) -> T:
        with self.guard:
            if self.blocked_until > time.monotonic():
                raise CacheUnavailable
        try:
            return operation()
        except (RedisError, OSError) as exc:
            with self.guard:
                warn = self.blocked_until <= time.monotonic()
                self.blocked_until = time.monotonic() + RETRY_SECONDS
            if warn:
                logger.warning("cache_unavailable", extra={"error_type": type(exc).__name__})
            raise CacheUnavailable from None

    def lookup(
        self,
        identity: str,
        domain: str,
        *,
        observe: bool = True,
        dependencies: tuple[str, ...] = (),
    ) -> CacheLookup:
        if any(dependency != "search-index" for dependency in dependencies):
            raise ValueError("Unknown cache dependency")

        def epoch(name: str) -> tuple[str, bytes]:
            generation_key = f"{self.namespace}:{name}:generation"
            generation = self.redis.get(generation_key)
            if generation is None:
                self.redis.set(generation_key, uuid.uuid4().hex, nx=True)
                generation = self.redis.get(generation_key)
            if (
                not isinstance(generation, bytes)
                or len(generation) != 32
                or any(byte not in b"0123456789abcdef" for byte in generation)
            ):
                # Never fall back to a fixed epoch after eviction/corruption.
                raise CacheUnavailable
            return generation_key, generation

        def operation() -> CacheLookup:
            generation_key, generation = epoch(domain)
            extra = tuple(epoch(name) for name in dependencies)
            # Include all dependency epochs in the response identity and fence
            # them again atomically with publication below.
            cache_identity = (
                json.dumps([identity, [value.decode("ascii") for _, value in extra]])
                if extra
                else identity
            )
            digest = hashlib.sha256(cache_identity.encode()).hexdigest()
            key = f"{self.namespace}:{domain}:{generation.decode('ascii')}:{digest}"
            lock_key = f"{key}:lock"
            body = self.redis.get(key)
            if body is not None:
                try:
                    if not isinstance(body, bytes) or len(body) > get_settings().cache_max_bytes:
                        raise ValueError("Invalid cached response size")
                    json.loads(body)
                except (ValueError, UnicodeError, RecursionError):
                    logger.warning("cache_entry_invalid")
                    body = None
            token = None
            if body is None:
                candidate = uuid.uuid4().hex
                if self.redis.set(lock_key, candidate, nx=True, ex=LOCK_SECONDS):
                    token = candidate
            return CacheLookup(generation_key, generation, key, lock_key, token, body, extra)

        name = {
            "public": "public_response",
            "admin-overview": "admin_overview",
            "admin-overview-panels": "admin_panels",
        }.get(domain, "other")
        try:
            result = self._run(operation)
        except CacheUnavailable:
            if observe:
                record_cache_read(name, "bypass", "cache_unavailable")
            raise
        if observe:
            record_cache_read(name, "hit" if result.body is not None else "miss")
        return result

    def publish(self, lookup: CacheLookup, body: bytes, ttl: int) -> bool:
        if lookup.token is None or len(body) > get_settings().cache_max_bytes:
            return False
        token = lookup.token
        return bool(
            self._run(
                lambda: self.redis.eval(
                    PUBLISH,
                    3 + len(lookup.dependencies),
                    lookup.generation_key,
                    *(key for key, _ in lookup.dependencies),
                    lookup.lock_key,
                    lookup.key,
                    lookup.generation,
                    *(generation for _, generation in lookup.dependencies),
                    token,
                    body,
                    ttl,
                )
            )
        )

    def release(self, lookup: CacheLookup) -> None:
        token = lookup.token
        if token:
            self._run(lambda: self.redis.eval(RELEASE, 1, lookup.lock_key, token))

    def invalidate(
        self, domain: str = "public", *, reasons: Collection[str] = ("unspecified",)
    ) -> None:
        if domain not in {"public", "operations", "search-index"}:
            raise ValueError("Unknown cache domain")
        try:
            self._run(
                lambda: self.redis.set(f"{self.namespace}:{domain}:generation", uuid.uuid4().hex)
            )
        except CacheUnavailable:
            record_cache_invalidation(domain, "cache_unavailable", reasons)
            raise
        record_cache_invalidation(domain, "success", reasons)
        logger.debug("cache_invalidated")

    def read_sequence(self, identity: str, start: int, stop: int) -> list[bytes] | None:
        """Immutable private data, outside the public response-cache generations."""
        key = f"{self.namespace}:feed:{identity}"

        def read() -> list[bytes] | None:
            with self.redis.pipeline() as pipe:
                pipe.exists(key)
                pipe.lrange(key, start, stop)
                exists, entries = pipe.execute()
            return cast(list[bytes], entries) if exists else None

        try:
            result = self._run(read)
        except CacheUnavailable:
            record_cache_read("feed_sequence", "bypass", "cache_unavailable")
            raise
        record_cache_read("feed_sequence", "hit" if result is not None else "miss")
        return result

    def write_sequence(self, identity: str, entries: list[bytes], ttl: int) -> None:
        if not entries or len(entries) > 500 or ttl <= 0:
            raise ValueError("Invalid feed sequence")
        key = f"{self.namespace}:feed:{identity}"
        self._run(
            lambda: self.redis.eval(
                "if redis.call('EXISTS', KEYS[1]) == 0 then "
                "redis.call('RPUSH', KEYS[1], unpack(ARGV, 2)); "
                "redis.call('EXPIRE', KEYS[1], ARGV[1]); end; return 1",
                1,
                key,
                ttl,
                *entries,
            )
        )


@lru_cache(maxsize=1)
def _cache_for_process(pid: int, configuration: str, database_url: str) -> ResponseCache:
    # Separate namespaces when multiple environments share Redis. No raw URLs in
    # cache keys/logs. PID isolates the short-lived breaker/locks after RQ forks.
    namespace = "devfeed:cache:v1:" + hashlib.sha256(database_url.encode()).hexdigest()[:16]
    redis = create_redis(
        get_settings(),
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
        retry=Retry(NoBackoff(), 0),
        max_connections=32,
    )
    return ResponseCache(redis, namespace)


def get_cache() -> ResponseCache:
    settings = get_settings()
    return _cache_for_process(os.getpid(), settings.model_dump_json(), settings.database_url)


def get_sitemap_cache() -> ResponseCache:
    """Give retained sitemap snapshots their own pool, deadline and circuit breaker."""
    cache = get_cache()
    with cache.guard:
        if cache.sitemap is None:
            cache.sitemap = ResponseCache(
                create_redis(
                    get_settings(),
                    socket_connect_timeout=1,
                    socket_timeout=3,
                    retry=Retry(NoBackoff(), 0),
                    max_connections=8,
                ),
                cache.namespace,
            )
        return cache.sitemap


def get_feed_sequence_cache() -> ResponseCache:
    """Keep required cursor storage independent of optional response-cache failures."""
    cache = get_cache()
    with cache.guard:
        if cache.feed_sequences is None:
            cache.feed_sequences = ResponseCache(
                create_redis(
                    get_settings(),
                    socket_connect_timeout=1,
                    socket_timeout=1,
                    retry=Retry(NoBackoff(), 0),
                    max_connections=32,
                ),
                cache.namespace,
            )
        return cache.feed_sequences


def close_cache() -> None:
    try:
        if _cache_for_process.cache_info().currsize:
            cache = get_cache()
            try:
                cache.redis.close()
            finally:
                try:
                    if cache.sitemap is not None:
                        cache.sitemap.redis.close()
                finally:
                    if cache.feed_sequences is not None:
                        cache.feed_sequences.redis.close()
    finally:
        # A close error must not leave disconnected clients available for reuse.
        _cache_for_process.cache_clear()


def invalidate_public_cache(*, reasons: Collection[str] = ("unspecified",)) -> None:
    if get_settings().cache_enabled:
        # A successful database commit must never be reported as failed just
        # because disposable cache invalidation failed. TTL bounds staleness.
        with suppress(CacheUnavailable):
            get_cache().invalidate(reasons=reasons)
    else:
        record_cache_invalidation("public", "disabled", reasons)


def invalidate_search_cache(*, public_change: bool) -> None:
    """Fence index completion separately, preserving unknown/raw SQL changes."""
    settings = get_settings()
    if public_change or not settings.search_cache_isolation_enabled:
        invalidate_public_cache(reasons=("search_index",))
    if settings.cache_enabled:
        with suppress(CacheUnavailable):
            get_cache().invalidate("search-index", reasons=("search_index",))
    else:
        record_cache_invalidation("search-index", "disabled", ("search_index",))
