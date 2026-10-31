"""An indexer-owned SQL pool preserves sequential outbox and reconnect behavior."""

import json
import time
from threading import Event
from unittest.mock import Mock

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import ArticleTag, SearchEvent, Tag
from devfeed_core.search_index import reconcile, sync_batch
from devfeed_search_indexer import runtime
from devfeed_search_indexer.runtime import indexer_database
from psycopg import Cursor
from sqlalchemy import delete, event, text

from tests.workers.test_automation_integration import seed

pytestmark = pytest.mark.integration


def tracing_cursor(commands):
    class TracedCursor(Cursor):
        def execute(self, query, *args, **kwargs):
            commands.append(
                query.as_string(self.connection) if hasattr(query, "as_string") else str(query)
            )
            return super().execute(query, *args, **kwargs)

    return TracedCursor


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
            # The runtime drops its owner connection before each idle wait.
            database_engine.dispose()
        assert counts == {"connections": 20, "statements": 40}
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
    settings = get_settings().model_copy(update={"search_indexer_connection_reuse_enabled": True})
    with indexer_database(settings) as factory:
        with factory() as session:
            pid = session.scalar(text("SELECT pg_backend_pid()"))
        with database() as observer:
            assert observer.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        with factory() as session:
            assert session.scalar(text("SELECT 1")) == 1
            assert session.scalar(text("SELECT pg_backend_pid()")) != pid


@pytest.mark.parametrize("active", [False, True])
def test_runtime_reuse_does_not_add_driver_commands_or_pin_idle_connections(
    database, monkeypatch, tmp_path, active
):
    with database.begin() as session:
        identifiers = []
        if active:
            _, article, _ = seed(session)
            article.publication_status, article.review_status = "published", "approved"
            tags = [Tag(name=f"Index tag {n}", slug=f"index-tag-{n}") for n in range(200)]
            session.add_all(tags)
            session.flush()
            session.add_all(ArticleTag(article_id=article.id, tag_id=tag.id) for tag in tags)
            identifiers = [tag.id for tag in tags]
        session.flush()
        session.execute(delete(SearchEvent))
    monkeypatch.setattr(get_settings(), "search_index_batch_size", 10)
    monkeypatch.setattr(runtime, "current", lambda: None)
    monkeypatch.setattr(runtime, "runtime_marker", lambda _: tmp_path / "heartbeat")
    reports = []
    for reuse in (False, True):
        with database.begin() as session:
            session.add_all(SearchEvent(kind="tags", entity_id=value) for value in identifiers)
        driver, live, turns = [], set(), []

        cursor_type = tracing_cursor(driver)

        settings = get_settings().model_copy(
            update={
                "database_pool_enabled": False,
                "search_indexer_connection_reuse_enabled": reuse,
            }
        )
        with indexer_database(settings) as factory:
            engine = factory.kw["bind"]

            def configure(_dialect, _record, _args, params, cursor_type=cursor_type):
                params["cursor_factory"] = cursor_type

            event.listen(engine, "do_connect", configure)
            event.listen(
                engine, "connect", lambda connection, _, live=live: live.add(id(connection))
            )
            event.listen(
                engine, "close", lambda connection, _, live=live: live.discard(id(connection))
            )
            stop = Event()

            def sync(*args, turns=turns, stop=stop):
                value = sync_batch(*args)
                turns.append(value)
                if len(turns) == 20:
                    stop.set()
                return value

            def wait(seconds, live=live):
                assert seconds == 1 and not live

            monkeypatch.setattr(runtime, "sync_batch", sync)
            monkeypatch.setattr(stop, "wait", wait)
            search = Mock()
            assert runtime._consume_index(stop, search, False, Mock(), factory, engine.dispose) == 0
            assert not live  # Burst cap also yields its slot under sustained work.
        assert sum(turns) == (200 if active else 0)
        assert sum(len(call.args[1]) for call in search.sync.call_args_list) == (
            200 if active else 0
        )
        reports.append(driver)
    # This observes psycopg itself, including configure_connection and pre-ping;
    # SQLAlchemy's application statement event omits both of those paths.
    assert len(reports[1]) <= len(reports[0])
    assert sum(query.strip() == ";" for query in reports[1]) == (19 if active else 0)
    assert sum(query.startswith("SET statement_timeout") for query in reports[1]) == (
        1 if active else 20
    )


def test_reconciliation_releases_owned_backend_before_remote_counts(database):
    settings = get_settings().model_copy(update={"search_indexer_connection_reuse_enabled": True})
    with indexer_database(settings) as factory:
        engine, live = factory.kw["bind"], set()
        event.listen(engine, "connect", lambda connection, _: live.add(id(connection)))
        event.listen(engine, "close", lambda connection, _: live.discard(id(connection)))
        search = Mock()

        def count(*_):
            assert not live
            return 0

        search.count.side_effect = count
        assert reconcile(factory, search, engine.dispose) == {
            "articles": 0,
            "topics": 0,
            "sources": 0,
            "tags": 0,
        }
