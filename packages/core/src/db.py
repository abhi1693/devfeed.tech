from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from devfeed_core import automation_events as _automation_events  # noqa: F401
from devfeed_core import notifications as _notifications  # noqa: F401
from devfeed_core.cache_events import AppSession
from devfeed_core.config import get_settings


@lru_cache
def get_engine():
    settings = get_settings()
    pool_options = (
        {"pool_size": settings.database_pool_size, "max_overflow": settings.database_max_overflow}
        if settings.database_pool_enabled
        else {"poolclass": NullPool}
    )
    engine = create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        **pool_options,
        connect_args={"connect_timeout": 5},
    )
    event.listen(engine, "connect", configure_connection)
    return engine


def configure_connection(connection, _record):
    # Session-mode PgBouncer rejects statement_timeout in the startup packet.
    # Set it after connecting, outside a transaction so rollback cannot undo it.
    autocommit = connection.autocommit
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '30s'")
    finally:
        connection.autocommit = autocommit


def session_factory() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), class_=AppSession, expire_on_commit=False)
