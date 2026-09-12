"""Independent dependency instances per service, with shared connection policy."""

from collections.abc import Callable, Generator
from functools import lru_cache

from devfeed_core.config import Settings
from devfeed_core.redis import create_redis
from redis import Redis
from sqlalchemy.orm import Session, sessionmaker


def session_dependency(factory: Callable[[], sessionmaker[Session]]):
    def get_session() -> Generator[Session, None, None]:
        with factory()() as session:
            yield session

    return get_session


def redis_dependency(settings: Callable[[], Settings]):
    @lru_cache
    def get_redis() -> Redis:
        return create_redis(settings())

    return get_redis
