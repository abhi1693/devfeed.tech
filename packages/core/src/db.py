from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from devfeed_core import automation_events as _automation_events  # noqa: F401
from devfeed_core import notifications as _notifications  # noqa: F401
from devfeed_core.cache_events import AppSession
from devfeed_core.config import get_settings


@lru_cache
def get_engine():
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=30000"},
    )


def session_factory() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), class_=AppSession, expire_on_commit=False)
