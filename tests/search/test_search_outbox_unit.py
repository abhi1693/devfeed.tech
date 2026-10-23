"""Search writer protocol over scoped events and projection reads.

Remote-write ordering, coalescing, and continuation commands are unit contracts;
PostgreSQL locking, selection, fanout query results and rollback are not emulated.
"""

import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import search_index as index
from devfeed_core.models import SearchEvent
from sqlalchemy.sql.dml import Delete, Insert

from tests.support.source_readiness import NOW, configuration


class Factory:
    def __init__(self, session):
        self.session, self.open = session, False

    @contextmanager
    def begin(self):
        self.open = True
        try:
            yield self.session
        finally:
            self.open = False

    __call__ = begin


def event(number, kind, identifier=None, **values):
    return SearchEvent(
        id=number,
        kind=kind,
        entity_id=identifier or uuid.uuid4(),
        created_at=NOW,
        fanout=False,
        **values,
    )


@pytest.fixture
def runtime(monkeypatch):
    session, engine = Mock(), Mock()
    session.scalar.return_value = True
    factory = Factory(session)
    monkeypatch.setattr(index, "get_settings", lambda: configuration(search_index_batch_size=10))
    project, invalidate = Mock(), Mock()
    monkeypatch.setattr(index, "documents", project)
    monkeypatch.setattr(index, "invalidate_public_cache", invalidate)
    return SimpleNamespace(
        session=session, engine=engine, factory=factory, project=project, invalidate=invalidate
    )


@pytest.mark.parametrize("stop", ["lease-busy", "empty"])
def test_sync_does_not_write_remotely_without_ownership_or_events(runtime, stop):
    runtime.session.scalar.return_value = stop != "lease-busy"
    runtime.session.scalars.return_value.all.return_value = []
    assert index.sync_batch(runtime.factory, runtime.engine) == 0
    runtime.engine.sync.assert_not_called()
    runtime.project.assert_not_called()
    runtime.invalidate.assert_not_called()
    runtime.session.execute.assert_not_called()


@pytest.mark.parametrize("kind", ["articles", "topics", "sources", "tags"])
def test_duplicate_events_share_one_projection_and_only_selected_events_are_acknowledged(
    runtime, kind
):
    identifier = uuid.uuid4()
    events = [event(11, kind, identifier), event(12, kind, identifier)]
    runtime.session.scalars.return_value.all.return_value = events
    current, removed = [{"id": str(identifier), "name": "Current projection"}], [str(uuid.uuid4())]
    runtime.project.return_value = current, removed

    def acknowledge(statement, *args):
        if isinstance(statement, Delete):
            runtime.engine.sync.assert_called_once_with(kind, current, removed)
            assert statement.compile().params == {"id_1": [11, 12]}

    runtime.session.execute.side_effect = acknowledge

    def invalidated(**kwargs):
        assert not runtime.factory.open
        assert kwargs == {"reasons": ("search_index",)}

    runtime.invalidate.side_effect = invalidated

    assert index.sync_batch(runtime.factory, runtime.engine) == 2

    runtime.project.assert_called_once_with(runtime.session, kind, {identifier})
    runtime.invalidate.assert_called_once()
    commands = [call.args[0] for call in runtime.session.execute.call_args_list]
    assert isinstance(commands[-1], Delete)
    assert len(commands) == (4 if kind == "articles" else 1)
    assert all(isinstance(command, Insert) for command in commands[:-1])


def test_remote_import_failure_leaves_events_unacknowledged_and_cache_untouched(runtime):
    runtime.session.scalars.return_value.all.return_value = [event(1, "articles")]
    runtime.project.return_value = [{"id": str(uuid.uuid4())}], []
    failure = RuntimeError("Typesense rejected the import")
    runtime.engine.sync.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        index.sync_batch(runtime.factory, runtime.engine)
    assert caught.value is failure
    runtime.session.execute.assert_not_called()
    runtime.invalidate.assert_not_called()
    assert not runtime.factory.open


@pytest.mark.parametrize("size", [0, 1, 10])
@pytest.mark.parametrize("cursor", [False, True])
def test_catalog_fanout_queues_selected_article_ids_and_only_full_pages_continue(
    runtime, size, cursor
):
    identifier = uuid.uuid4()
    previous = uuid.UUID(int=1) if cursor else None
    row = SearchEvent(
        id=20, kind="topics", entity_id=identifier, fanout=True, after_id=previous, created_at=NOW
    )
    ids = [uuid.UUID(int=i) for i in range(2, 2 + size)]
    runtime.session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [row]),
        SimpleNamespace(all=lambda: ids),
    ]
    runtime.project.return_value = [], [str(identifier)]
    assert index.sync_batch(runtime.factory, runtime.engine) == 1
    writes = [
        call for call in runtime.session.execute.call_args_list if isinstance(call.args[0], Insert)
    ]
    assert len(writes) == int(bool(ids))
    if ids:
        assert writes[0].args[1] == [{"kind": "articles", "entity_id": value} for value in ids]
    if size == 10:
        continuation = runtime.session.add.call_args.args[0]
        assert continuation.kind == "topics" and continuation.entity_id == identifier
        assert continuation.fanout and continuation.after_id == ids[-1]
    else:
        runtime.session.add.assert_not_called()


def test_reconciliation_releases_the_read_scope_before_remote_counts(runtime, monkeypatch, caplog):
    counts = {"articles": 4, "topics": 3, "sources": 2, "tags": 1}
    monkeypatch.setattr(index, "_visible_counts", Mock(return_value=counts))
    monkeypatch.setattr(index, "_update_outbox_metrics", Mock())

    def count(kind):
        assert not runtime.factory.open
        return counts[kind] + 2

    runtime.engine.count.side_effect = count
    runtime.engine.collection.side_effect = lambda kind: "devfeed_" + kind
    with caplog.at_level("INFO", logger=index.__name__):
        assert index.reconcile(runtime.factory, runtime.engine) == counts
    assert [record.count_delta for record in caplog.records] == [2, 2, 2, 2]
    assert {record.collection for record in caplog.records} == {
        "devfeed_" + kind for kind in counts
    }


@pytest.mark.parametrize("age", [None, "past", "future"])
def test_outbox_metrics_distinguish_empty_backlog_and_clamp_clock_skew(monkeypatch, age):
    metrics = {name: Mock() for name in ("search_outbox", "search_oldest")}
    monkeypatch.setattr(index, "_metric_instruments", lambda: metrics)
    clock = Mock()
    clock.now.return_value = NOW
    monkeypatch.setattr(index, "datetime", clock)
    oldest = None if age is None else NOW + timedelta(seconds=5 if age == "future" else -7)
    session = Mock()
    session.execute.return_value.one.return_value = (0 if age is None else 2, oldest)
    index._update_outbox_metrics(session)
    metrics["search_outbox"].set.assert_called_once_with(0 if age is None else 2)
    metrics["search_oldest"].set.assert_called_once_with(7 if age == "past" else 0)


def test_backfill_reports_total_outbox_commands_from_each_projection(runtime):
    runtime.session.scalar.side_effect = [5, 3, 2, 0]
    assert index.backfill(runtime.factory) == 10
    assert runtime.session.scalar.call_count == 4
    runtime.engine.sync.assert_not_called()


def test_visible_projection_counts_use_revision_owned_cache_names_without_retaining_rows(
    runtime, monkeypatch
):
    runtime.session.scalar.side_effect = [4, 3, 2, 0]
    requests = []

    def cached(session, revisions, loader):
        assert session is runtime.session
        requests.append(revisions)
        return loader()

    monkeypatch.setattr(index, "snapshot", cached)
    assert index._visible_counts(runtime.session) == {
        "articles": 4,
        "topics": 3,
        "sources": 2,
        "tags": 0,
    }
    assert requests == [("search_tag_visibility", "topics"), ("search_tag_visibility",)]
