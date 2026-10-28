"""Direct search counts rank all authorized candidates without PG aggregates."""

import asyncio
from datetime import UTC, datetime

import pytest
from devfeed_core import search_suggestions
from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.search_suggestions import SearchSuggestionAnalytics, query_hash


class Client:
    def __init__(self, complete=True):
        self.complete = complete

    async def query(self, statement, params):
        assert "analytics_backfill_progress" in statement
        assert params["import_id"] == "direct-v1"
        return [{"complete": int(self.complete)}]


class Counts:
    def __init__(self, values):
        self.values = values
        self.pages = []

    async def search_counts(self, ids):
        self.pages.append(ids)
        return [
            {
                "query_hash": identity,
                "successful_count": self.values[identity],
                "first_seen": "2026-01-01T00:00:00Z",
                "last_seen": "2026-01-02T00:00:00Z",
            }
            for identity in ids
            if identity in self.values
        ]


def test_ranking_considers_all_pages_and_preserves_tie_order(monkeypatch):
    async def run():
        items = [
            {
                "query_hash": f"{i:064x}",
                "query": f"search {i:04d}",
                "status": "approved",
                "reviewed_at": None,
                "review_note": None,
            }
            for i in range(1201)
        ]
        counts = {item["query_hash"]: 1 for item in items}
        counts[items[-1]["query_hash"]] = 100
        counts[items[800]["query_hash"]] = 30
        counts[items[100]["query_hash"]] = 30
        pages = []

        def read_page(_session, *, cursor, prefix, status):
            assert prefix == "search" and status == "approved"
            result = [item for item in items if cursor is None or item["query_hash"] > cursor][:500]
            pages.append(result)
            return result

        monkeypatch.setattr(search_suggestions, "_metadata_page", read_page)
        analytics = SearchSuggestionAnalytics(Client())
        analytics.store = store = Counts(counts)
        result = await analytics.rank(
            object(), prefix=" SEARCH ", status="approved", limit=3, minimum=2
        )
        assert [item["query"] for item in result] == ["search 1200", "search 0100", "search 0800"]
        assert [len(page) for page in pages] == [500, 500, 201]
        assert [len(page) for page in store.pages] == [500, 500, 201]

    asyncio.run(run())


def test_import_readiness_failure_prevents_source_reads(monkeypatch):
    async def run():
        monkeypatch.setattr(
            search_suggestions,
            "_metadata_page",
            lambda *_args, **_kwargs: pytest.fail("queried unready metadata"),
        )
        analytics = SearchSuggestionAnalytics(Client(complete=False))
        with pytest.raises(AnalyticsUnavailable, match="history"):
            await analytics.rank(object(), prefix="python", status="approved")
        assert await analytics.rank(object(), prefix="  ", status="approved") == []

    asyncio.run(run())


def test_direct_observation_is_identity_free_and_carries_no_query_text():
    async def run():
        events = []

        class Store:
            async def search_events(self, rows):
                events.extend(rows)

        analytics = SearchSuggestionAnalytics(Client())
        analytics.store = Store()
        digest = query_hash("private query")
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await analytics.record(digest, now=now)
        assert len(events) == 1 and events[0]["query_hash"] == digest
        assert events[0]["occurred_at"] == now and set(events[0]) == {
            "query_hash",
            "event_id",
            "occurred_at",
        }
        assert "private query" not in str(events)

    asyncio.run(run())


def test_metadata_pages_create_use_and_close_the_session_before_clickhouse():
    import threading
    from types import SimpleNamespace

    async def run():
        loop_thread = threading.get_ident()
        events = []
        digest = query_hash("search")
        record = {
            "query_hash": digest,
            "query": "search",
            "status": "approved",
            "reviewed_at": None,
            "review_note": None,
        }

        class Session:
            def __init__(self):
                self.owner = threading.get_ident()
                assert self.owner != loop_thread
                events.append("create")

            def __enter__(self):
                assert self.owner == threading.get_ident()
                events.append("open")
                return self

            def execute(self, _statement):
                assert self.owner == threading.get_ident()
                events.append("query")
                return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [record]))

            def __exit__(self, *_args):
                assert self.owner == threading.get_ident()
                events.append("close")

        class CheckedCounts(Counts):
            async def search_counts(self, ids):
                assert events == ["create", "open", "query", "close"]
                events.append("clickhouse")
                return await super().search_counts(ids)

        analytics = SearchSuggestionAnalytics(Client())
        analytics.store = CheckedCounts({digest: 2})
        rows = await analytics.rank(Session, prefix="search", status="approved")
        assert rows[0]["successful_count"] == 2
        assert events[-1] == "clickhouse"

    asyncio.run(run())


@pytest.mark.parametrize(
    "response,valid",
    [
        ([], False),
        ([{"successful_count": -1}], False),
        ([{"successful_count": "2"}], False),
        ([{"successful_count": 2, "first_seen": "first", "last_seen": "last"}], True),
    ],
)
def test_single_suggestion_metrics_validate_native_counts(response, valid):
    async def run():
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        analytics = SearchSuggestionAnalytics(Client())
        analytics.store = SimpleNamespace(search_counts=AsyncMock(return_value=response))
        if valid:
            assert await analytics.get("a" * 64) == {
                "successful_count": 2,
                "first_seen_at": "first",
                "last_seen_at": "last",
            }
        else:
            with pytest.raises(AnalyticsUnavailable):
                await analytics.get("a" * 64)

    asyncio.run(run())


@pytest.mark.parametrize("count", [None, -1, "1"])
def test_rank_rejects_malformed_counts_instead_of_returning_misleading_volume(monkeypatch, count):
    async def run():
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            search_suggestions,
            "_metadata_page",
            lambda *_args, **_kwargs: [{"query_hash": "a" * 64, "query": "search"}],
        )
        analytics = SearchSuggestionAnalytics(Client())
        analytics.store = SimpleNamespace(
            search_counts=AsyncMock(
                return_value=[{"query_hash": "a" * 64, "successful_count": count}]
            )
        )
        with pytest.raises(AnalyticsUnavailable):
            await analytics.rank(object())

    asyncio.run(run())


def test_empty_pages_and_missing_metrics_do_not_invent_successful_searches(monkeypatch):
    async def run():
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        analytics = SearchSuggestionAnalytics(Client())
        analytics.store = SimpleNamespace(search_counts=AsyncMock(return_value=[]))
        monkeypatch.setattr(search_suggestions, "_metadata_page", lambda *_args, **_kwargs: [])
        assert await analytics.rank(object()) == []
        analytics.store.search_counts.assert_not_called()
        monkeypatch.setattr(
            search_suggestions,
            "_metadata_page",
            lambda *_args, **_kwargs: [{"query_hash": "a" * 64, "query": "search"}],
        )
        assert await analytics.rank(object()) == []

    asyncio.run(run())


def test_metadata_page_can_read_all_moderation_states_and_review_unknown_hashes_safely():
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock

    from devfeed_core.models import SearchQueryStat

    rows = [{"query_hash": "a" * 64, "query": "search"}]
    session = Mock()
    session.execute.return_value.mappings.return_value.all.return_value = rows

    @contextmanager
    def factory():
        yield session

    assert search_suggestions._metadata_page(factory, cursor=None, prefix=None, status=None) == rows
    assert "WHERE" not in str(session.execute.call_args.args[0])
    search_suggestions._metadata_page(factory, cursor="a" * 64, prefix="search", status="approved")
    bound = session.execute.call_args.args[0].compile().params
    assert "a" * 64 in bound.values() and "search" in bound.values()
    session.get.return_value = None
    with pytest.raises(ValueError):
        search_suggestions.review_query(
            session, "BAD", status="approved", reviewer="admin", note=None
        )
    with pytest.raises(LookupError):
        search_suggestions.review_query(
            session, "a" * 64, status="approved", reviewer="admin", note=None
        )
    value = SimpleNamespace(status="pending", reviewed_at=None, reviewed_by=None, review_note=None)
    session.get.return_value = value
    assert (
        search_suggestions.review_query(
            session, "a" * 64, status="rejected", reviewer="admin", note="Private"
        )
        is value
    )
    session.get.assert_called_with(SearchQueryStat, "a" * 64)
    assert (value.status, value.reviewed_by, value.review_note) == ("rejected", "admin", "Private")
    assert value.reviewed_at.tzinfo == UTC


@pytest.mark.parametrize("value", ["x" * 201, "word " * 21, "!!!", ""])
def test_search_query_limits_reject_invalid_metadata_before_any_io(value):
    with pytest.raises(ValueError):
        search_suggestions.normalize_query(value)
