"""Bounded reporting connections, isolated from interactive API requests."""

import time
from contextlib import contextmanager
from functools import lru_cache

from devfeed_core.config import get_settings
from devfeed_core.db import create_database_engine
from sqlalchemy import event
from sqlalchemy.exc import TimeoutError as DatabaseTimeout
from sqlalchemy.orm import sessionmaker


@contextmanager
def report_budget(session, seconds=30):
    """Bound a calculation even when a dedicated worker supplies its own engine."""
    connection = session.connection()
    deadline = time.monotonic() + seconds

    def bound(connection, cursor, statement, parameters, context, many):
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise DatabaseTimeout("Overview calculation exceeded its time budget")
        cursor.execute(f"SET LOCAL statement_timeout = '{min(10000, remaining)}ms'")

    event.listen(connection, "before_cursor_execute", bound)
    try:
        yield
    finally:
        event.remove(connection, "before_cursor_execute", bound)


@lru_cache(maxsize=1)
def reporting_engine():
    # A session-mode pooler pins a server connection for every idle client.
    # Document reads and publishers release their pooler slot after each transaction.
    engine = create_database_engine(
        get_settings().model_copy(update={"database_pool_enabled": False})
    )

    @event.listens_for(engine, "before_cursor_execute")
    def bound_report(connection, cursor, statement, parameters, context, many):
        deadline = connection.info.get("report_deadline")
        if deadline is None:
            return
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
