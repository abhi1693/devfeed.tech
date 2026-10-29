"""Bounded independent source connections for analytics maintenance."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from devfeed_core.analytics import SqlRecord


def postgres_connections(
    database_url: str, *, connect_timeout: int = 5
) -> Callable[[], AbstractAsyncContextManager[AsyncConnection[SqlRecord]]]:
    """Own and close each source connection independently of request pools."""
    if not isinstance(database_url, str) or not database_url:
        raise ValueError("An analytics database URL is required")
    if type(connect_timeout) is not int or not 1 <= connect_timeout <= 60:
        raise ValueError("Invalid PostgreSQL connect timeout")
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    @asynccontextmanager
    async def connect() -> AsyncIterator[AsyncConnection[SqlRecord]]:
        async with await psycopg.AsyncConnection.connect(
            dsn, autocommit=True, row_factory=dict_row, connect_timeout=connect_timeout
        ) as connection:
            yield connection

    return connect
