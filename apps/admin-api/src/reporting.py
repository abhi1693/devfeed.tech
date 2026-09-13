"""Bounded reporting connections, isolated from interactive API requests."""

import time
from functools import lru_cache

from devfeed_core.config import get_settings
from devfeed_core.db import create_database_engine
from sqlalchemy import event
from sqlalchemy.exc import TimeoutError as DatabaseTimeout
from sqlalchemy.orm import sessionmaker


@lru_cache(maxsize=1)
def reporting_engine():
    # A session-mode pooler pins a server connection for every idle client.
    # Reports are occasional: release their pooler slot after each refresh.
    engine = create_database_engine(
        get_settings().model_copy(update={"database_pool_enabled": False})
    )

    @event.listens_for(engine, "before_cursor_execute")
    def bound_report(connection, cursor, statement, parameters, context, many):
        deadline = connection.info.setdefault("report_deadline", time.monotonic() + 30)
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise DatabaseTimeout("Overview calculation exceeded its 30-second budget")
        # The server cancels the last statement too, not just the next Python call.
        cursor.execute(f"SET LOCAL statement_timeout = '{min(10000, remaining)}ms'")

    return engine


def reporting_sessions():
    return sessionmaker(reporting_engine(), expire_on_commit=False)


def close_reporting():
    if reporting_engine.cache_info().currsize:
        reporting_engine().dispose()
    reporting_engine.cache_clear()
