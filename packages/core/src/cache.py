"""Disposable Redis response cache; never owns database or RQ state."""

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache

from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from devfeed_core.config import get_settings
from devfeed_core.redis import create_redis

logger = logging.getLogger(__name__)
LOCK_SECONDS = 30
RETRY_SECONDS = 5

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

    def lookup(self, identity: str, domain: str) -> CacheLookup:
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

        return self._run(operation)

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

    def invalidate(self, domain: str = "public") -> None:
        if domain not in {"public", "operations"}:
            raise ValueError("Unknown cache domain")
        self._run(lambda: self.redis.set(f"{self.namespace}:{domain}:generation", uuid.uuid4().hex))
        logger.debug("cache_invalidated")


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


def invalidate_public_cache() -> None:
    if get_settings().cache_enabled:
        # A successful database commit must never be reported as failed just
        # because disposable cache invalidation failed. TTL bounds staleness.
        with suppress(CacheUnavailable):
            get_cache().invalidate()
