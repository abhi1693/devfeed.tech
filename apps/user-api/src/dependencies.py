from collections.abc import Callable
from dataclasses import replace
from typing import Annotated

from devfeed_core.config import get_settings
from devfeed_core.db import read_session_factory, session_factory
from devfeed_http.dependencies import redis_dependency, session_dependency
from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

# Each service owns its cached clients and dependency identity.
get_session = session_dependency(session_factory)
get_redis = redis_dependency(get_settings)
database_session = Depends(get_session, scope="function")
DB = Annotated[Session, database_session]
get_read_session = session_dependency(read_session_factory)
read_database_session = replace(database_session, dependency=get_read_session)
ReadDB = Annotated[Session, read_database_session]


def get_session_factory() -> sessionmaker[Session]:
    return session_factory()


DBFactory = Annotated[Callable[[], Session], Depends(get_session_factory)]


def get_read_session_factory() -> sessionmaker[Session]:
    return read_session_factory()


ReadDBFactory = Annotated[Callable[[], Session], Depends(get_read_session_factory)]
