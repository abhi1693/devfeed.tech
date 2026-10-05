"""Shutdown shared cache, database, and Redis clients without opening them."""

from contextlib import ExitStack
from typing import Protocol

from sqlalchemy.engine import Engine

from devfeed_core.cache import close_cache
from devfeed_core.db import get_engine
from redis import Redis


class CacheInfo(Protocol):
    @property
    def currsize(self) -> int: ...


class CachedRedisProvider(Protocol):
    def __call__(self) -> Redis: ...

    def cache_info(self) -> CacheInfo: ...

    def cache_clear(self) -> None: ...


def close_shared_clients(
    redis_provider: CachedRedisProvider, *additional_redis: CachedRedisProvider
) -> None:
    """Close initialized shared clients; preserve each app's Redis provider."""

    def close_engine():
        if get_engine.cache_info().currsize:
            engine: Engine = get_engine()
            engine.dispose()

    def close_redis(provider: CachedRedisProvider):
        if provider.cache_info().currsize:
            provider().close()

    with ExitStack() as stack:
        for provider in (redis_provider, *additional_redis):
            stack.callback(provider.cache_clear)
            stack.callback(close_redis, provider)
        stack.callback(close_engine)
        stack.callback(close_cache)
