"""An indexer-owned SQL pool preserves sequential outbox and reconnect behavior."""

import json
import time
from unittest.mock import Mock

import pytest
from devfeed_core.config import get_settings
from devfeed_core.search_index import sync_batch
from devfeed_search_indexer.runtime import indexer_database
from sqlalchemy import event, text

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("reuse", [False, True])
def test_idle_indexer_connections_are_bounded_without_delaying_polls(database, reuse):
    settings = get_settings().model_copy(
        update={
            "database_pool_enabled": False,
            "search_indexer_connection_reuse_enabled": reuse,
        }
    )
    with indexer_database(settings) as factory:
        database_engine = factory.kw["bind"]
        counts = {"connections": 0, "statements": 0}

        def connected(*_):
            counts["connections"] += 1

        def statement(*_):
            counts["statements"] += 1

        event.listen(database_engine, "connect", connected)
        event.listen(database_engine, "before_cursor_execute", statement)
        cpu, wall = time.process_time(), time.perf_counter()
        for _ in range(20):
            assert sync_batch(factory, Mock()) == 0
        assert counts == {"connections": 1 if reuse else 20, "statements": 40}
        print(
            json.dumps(
                {
                    "reuse": reuse,
                    **counts,
                    "turns": 20,
                    "cpu_seconds": time.process_time() - cpu,
                    "wall_seconds": time.perf_counter() - wall,
                }
            )
        )


def test_indexer_replaces_an_idle_connection_whose_backend_disappears(database):
    with indexer_database(get_settings()) as factory:
        with factory() as session:
            pid = session.scalar(text("SELECT pg_backend_pid()"))
        with database() as observer:
            assert observer.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        with factory() as session:
            assert session.scalar(text("SELECT 1")) == 1
            assert session.scalar(text("SELECT pg_backend_pid()")) != pid
