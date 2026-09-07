from collections.abc import Generator
from functools import lru_cache
from typing import Annotated

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from fastapi import Depends
from redis import Redis
from redis.backoff import ExponentialWithJitterBackoff
from redis.retry import Retry
from sqlalchemy.orm import Session


def get_session() -> Generator[Session, None, None]:
    with session_factory()() as session:
        yield session


DB = Annotated[Session, Depends(get_session)]


@lru_cache
def get_redis() -> Redis:
    # Preserve the Redis 6 outage budget across client-library upgrades.
    return Redis.from_url(
        get_settings().redis_url,
        socket_connect_timeout=3,
        socket_timeout=3,
        retry=Retry(ExponentialWithJitterBackoff(base=1, cap=10), retries=3),
    )
