from typing import Annotated

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_http.dependencies import redis_dependency, session_dependency
from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

# Each service owns its cached clients and dependency identity.
get_session = session_dependency(session_factory)
get_redis = redis_dependency(get_settings)
database_session = Depends(get_session, scope="function")
DB = Annotated[Session, database_session]


def get_session_factory() -> sessionmaker[Session]:
    return session_factory()


DBFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]


async def read_metadata(factory, operation, *args):
    """Create, use, and close each synchronous session in the same worker."""

    def read():
        with factory() as session:
            return operation(session, *args)

    return await run_in_threadpool(read)
