from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from devfeed_core import automation_events as _automation_events  # noqa: F401
from devfeed_core import notifications as _notifications  # noqa: F401
from devfeed_core.cache_events import AppSession
from devfeed_core.config import Settings, get_settings


@lru_cache
def get_engine():
    return create_database_engine(get_settings())


def create_database_engine(settings: Settings):
    pool_options = (
        {
            "pool_size": settings.database_pool_size,
            "max_overflow": settings.database_max_overflow,
            "pool_timeout": settings.database_pool_timeout_seconds,
            "pool_recycle": settings.database_pool_recycle_seconds,
        }
        if settings.database_pool_enabled
        else {"poolclass": NullPool}
    )
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        **pool_options,
        # These libpq settings apply to both pooled APIs and NullPool workers.
        # connect_timeout only covers establishment; TCP controls bound loss of
        # an established peer. Pre-ping/invalidation then replace dead sockets.
        connect_args={
            "connect_timeout": settings.database_connect_timeout_seconds,
            "keepalives": 1,
            "keepalives_idle": settings.database_keepalives_idle_seconds,
            "keepalives_interval": settings.database_keepalives_interval_seconds,
            "keepalives_count": settings.database_keepalives_count,
            "tcp_user_timeout": settings.database_tcp_user_timeout_ms,
        },
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


def database_revision(session: Session):
    # Health checks use the real application pool. The local timeout is rolled
    # back when the request session closes, preserving normal query budgets.
    session.execute(text("SET LOCAL statement_timeout = '2s'"))
    return session.scalar(text("SELECT version_num FROM alembic_version"))
