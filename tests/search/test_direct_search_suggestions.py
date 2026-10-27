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
