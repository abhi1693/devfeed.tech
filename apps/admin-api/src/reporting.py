"""Bounded reporting connections, isolated from interactive API requests."""

from functools import lru_cache

from devfeed_core.config import get_settings
from devfeed_core.db import create_database_engine
from sqlalchemy.orm import sessionmaker


@lru_cache(maxsize=1)
def reporting_engine():
    # A session-mode pooler pins a server connection for every idle client.
    # Reports are occasional: release their pooler slot after each refresh.
    return create_database_engine(
        get_settings().model_copy(update={"database_pool_enabled": False})
    )


def reporting_sessions():
    return sessionmaker(reporting_engine(), expire_on_commit=False)


def close_reporting():
    if reporting_engine.cache_info().currsize:
        reporting_engine().dispose()
    reporting_engine.cache_clear()
