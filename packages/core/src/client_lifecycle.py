"""Shutdown shared cache, database, and Redis clients without opening them."""

from typing import Protocol

from sqlalchemy.engine import Engine

from devfeed_core.cache import close_cache
from devfeed_core.db import get_engine
from redis import Redis


class CacheInfo(Protocol):
    currsize: int


class CachedRedisProvider(Protocol):
    def __call__(self) -> Redis: ...

    def cache_info(self) -> CacheInfo: ...

    def cache_clear(self) -> None: ...


def close_shared_clients(redis_provider: CachedRedisProvider) -> None:
    """Close initialized shared clients; preserve each app's Redis provider."""
    close_cache()
    if get_engine.cache_info().currsize:
        engine: Engine = get_engine()
        engine.dispose()
    if redis_provider.cache_info().currsize:
        redis_provider().close()
    redis_provider.cache_clear()
