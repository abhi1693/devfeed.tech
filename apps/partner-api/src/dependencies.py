from collections.abc import Callable
from typing import Annotated

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_http.dependencies import redis_dependency, session_dependency
from fastapi.params import Depends
from sqlalchemy.orm import Session, sessionmaker

# Each service owns its cached clients and dependency identity.
get_session = session_dependency(session_factory)
get_redis = redis_dependency(get_settings)
database_session = Depends(get_session, scope="function")
DB = Annotated[Session, database_session]


def get_session_factory() -> sessionmaker[Session]:
    return session_factory()


DBFactory = Annotated[Callable[[], Session], Depends(get_session_factory)]
