from functools import lru_cache
from typing import Annotated

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.redis import create_redis
from devfeed_http.dependencies import redis_dependency
from fastapi import Depends, Request
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy.orm import Session


# Each service owns its cached clients and dependency identity.
def get_session(request: Request):
    with session_factory()() as session:
        request.state.public_read_session = session
        yield session


get_redis = redis_dependency(get_settings)


@lru_cache
def get_rate_limit_redis() -> Redis:
    # Analytics must not hold an admission slot through cache retry backoff.
    return create_redis(
        get_settings(),
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
        retry=Retry(NoBackoff(), retries=0),
    )


# Return the connection after serialization, before route-cache Redis I/O or
# response transmission. Only this public API has no streaming DB consumers.
database_session = Depends(get_session, scope="function")
DB = Annotated[Session, database_session]
