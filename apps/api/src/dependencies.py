from typing import Annotated

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_http.dependencies import redis_dependency
from fastapi import Depends, Request
from sqlalchemy.orm import Session


# Each service owns its cached clients and dependency identity.
def get_session(request: Request):
    with session_factory()() as session:
        request.state.public_read_session = session
        yield session


get_redis = redis_dependency(get_settings)
# Return the connection after serialization, before route-cache Redis I/O or
# response transmission. Only this public API has no streaming DB consumers.
DB = Annotated[Session, Depends(get_session, scope="function")]
