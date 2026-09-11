from typing import Annotated

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_http.dependencies import redis_dependency, session_dependency
from fastapi import Depends
from sqlalchemy.orm import Session

# Each service owns its cached clients and dependency identity.
get_session = session_dependency(session_factory)
get_redis = redis_dependency(get_settings)
DB = Annotated[Session, Depends(get_session)]
