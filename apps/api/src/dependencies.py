from collections.abc import Generator
from functools import lru_cache
from typing import Annotated

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from fastapi import Depends
from redis import Redis
from sqlalchemy.orm import Session


def get_session() -> Generator[Session, None, None]:
    with session_factory()() as session:
        yield session


DB = Annotated[Session, Depends(get_session)]


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, socket_connect_timeout=3, socket_timeout=3)
