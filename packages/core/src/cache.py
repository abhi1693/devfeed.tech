"""Disposable Redis response cache; never owns database or RQ state."""

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Collection
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache

from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from devfeed_core.config import get_settings
from devfeed_core.redis import create_redis
from devfeed_core.telemetry import current

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
            "cache.domain": domain if domain in {"public", "operations"} else "other",
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
if redis.call('GET', KEYS[1]) == ARGV[1]
   and redis.call('GET', KEYS[2]) == ARGV[2] then
    redis.call('SET', KEYS[3], ARGV[3], 'EX', ARGV[4])
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


class ResponseCache:
    def __init__(self, redis, namespace: str):
        self.redis = redis
        self.namespace = namespace
        self.blocked_until = 0.0
        self.guard = threading.Lock()

    def _run(self, operation):
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

    def lookup(self, identity: str, domain: str, *, observe=True) -> CacheLookup:
        def operation():
            generation_key = f"{self.namespace}:{domain}:generation"
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
            digest = hashlib.sha256(identity.encode()).hexdigest()
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
            return CacheLookup(generation_key, generation, key, lock_key, token, body)

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
        return bool(
            self._run(
                lambda: self.redis.eval(
                    PUBLISH,
                    3,
                    lookup.generation_key,
                    lookup.lock_key,
                    lookup.key,
                    lookup.generation,
                    lookup.token,
                    body,
                    ttl,
                )
            )
        )

    def release(self, lookup: CacheLookup) -> None:
        if lookup.token:
            self._run(lambda: self.redis.eval(RELEASE, 1, lookup.lock_key, lookup.token))

    def invalidate(
        self, domain: str = "public", *, reasons: Collection[str] = ("unspecified",)
    ) -> None:
        if domain not in {"public", "operations"}:
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

        def read():
            with self.redis.pipeline() as pipe:
                exists, entries = pipe.exists(key).lrange(key, start, stop).execute()
            return entries if exists else None

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


def close_cache() -> None:
    if _cache_for_process.cache_info().currsize:
        get_cache().redis.close()
    _cache_for_process.cache_clear()


def invalidate_public_cache(*, reasons: Collection[str] = ("unspecified",)) -> None:
    if get_settings().cache_enabled:
        # A successful database commit must never be reported as failed just
        # because disposable cache invalidation failed. TTL bounds staleness.
        with suppress(CacheUnavailable):
            get_cache().invalidate(reasons=reasons)
    else:
        record_cache_invalidation("public", "disabled", reasons)
