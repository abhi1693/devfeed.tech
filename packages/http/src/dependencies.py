"""Independent dependency instances per service, with shared connection policy."""

from __future__ import annotations

from collections.abc import Callable, Generator
from functools import lru_cache

from devfeed_core.client_lifecycle import CachedRedisProvider
from devfeed_core.config import Settings
from devfeed_core.redis import create_redis
from redis import Redis
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool


def session_dependency(
    factory: Callable[[], sessionmaker[Session]],
) -> Callable[[], Generator[Session, None, None]]:
    def get_session() -> Generator[Session, None, None]:
        with factory()() as session:
            yield session

    return get_session


def redis_dependency(settings: Callable[[], Settings]) -> CachedRedisProvider:
    @lru_cache
    def get_redis() -> Redis:
        return create_redis(settings())

    return get_redis


async def run_database[R, *Args](
    factory: Callable[[], Session],
    operation: Callable[[Session, *Args], R],
    *args: *Args,
) -> R:
    """Create, use and close a source session within one request worker."""

    def read() -> R:
        with factory() as session:
            return operation(session, *args)

    return await run_in_threadpool(read)
