"""Independent dependency instances per service, with shared connection policy."""

from collections.abc import Callable, Generator
from functools import lru_cache

from devfeed_core.config import Settings
from redis import Redis
from redis.backoff import ExponentialWithJitterBackoff
from redis.retry import Retry
from sqlalchemy.orm import Session, sessionmaker


def session_dependency(factory: Callable[[], sessionmaker[Session]]):
    def get_session() -> Generator[Session, None, None]:
        with factory()() as session:
            yield session

    return get_session


def redis_dependency(settings: Callable[[], Settings]):
    @lru_cache
    def get_redis() -> Redis:
        # Bound outage retries explicitly across redis-py upgrades.
        return Redis.from_url(
            settings().redis_url,
            socket_connect_timeout=3,
            socket_timeout=3,
            retry=Retry(ExponentialWithJitterBackoff(base=1, cap=10), retries=3),
        )

    return get_redis
