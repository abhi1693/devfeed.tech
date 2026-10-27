"""Historical pages can be retried without checkpointing incomplete work."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

import pytest
from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.analytics_backfill import AnalyticsBackfill, BackfillSource


class Cursor:
    def __init__(self, records):
        self.records = records

    async def fetchall(self):
        return [{"record": record.copy()} for record in self.records]


class Postgres:
    def __init__(self, records):
        self.records = records
        self.in_transaction = False

    @asynccontextmanager
    async def transaction(self):
        assert not self.in_transaction
        self.in_transaction = True
        try:
            yield
        finally:
            self.in_transaction = False

    async def execute(self, _statement, params=None):
        if params is None:
            return None
        cursor = params[0] if len(params) > 1 else None
        return Cursor(
            [item for item in self.records if cursor is None or item["id"] > cursor][: params[-1]]
        )


class ClickHouse:
    def __init__(self, source):
        self.source = source
        self.progress = {}
        self.fail_checkpoint = False

    async def query(self, _statement, params):
        row = self.progress.get((params["import_id"], params["source"]))
        return [row] if row else []

    async def insert(self, table, rows):
        assert table == "analytics_backfill_progress" and not self.source.in_transaction
        if self.fail_checkpoint:
            raise RuntimeError("checkpoint unavailable")
        row = rows[0]
        self.progress[(row["import_id"], row["source"])] = row


def test_import_resumes_after_failed_checkpoint_without_duplicating_facts():
    async def run():
        records = [{"id": str(uuid.UUID(int=i)), "value": i} for i in range(1, 6)]
        pg = Postgres(records)
        ch = ClickHouse(pg)
        facts = {}

        async def write(batch):
            assert not pg.in_transaction
            facts.update({row["id"]: row["value"] for row in batch})

        source = BackfillSource("articles", ("id", "value"), ("id",), write)
        importer = AnalyticsBackfill(ch, "initial", [source], page_size=2)
        ch.fail_checkpoint = True
        with pytest.raises(RuntimeError, match="checkpoint"):
            await importer.scan_one(pg, "articles")
        assert len(facts) == 2 and not ch.progress
        assert not await importer.complete(["articles"])
        ch.fail_checkpoint = False
        assert await importer.scan_one(pg, "articles")
        assert json.loads(ch.progress[("initial", "articles")]["cursor"]) == [records[1]["id"]]
        assert await importer.scan_one(pg, "articles")
        assert await importer.scan_one(pg, "articles")
        assert not await importer.scan_one(pg, "articles")
        assert len(facts) == 5
        assert await importer.complete(["articles"])

    asyncio.run(run())


def test_failed_writer_or_invalid_checkpoint_never_advances():
    async def run():
        pg = Postgres([{"id": "a", "value": 1}])
        ch = ClickHouse(pg)

        async def fail(_batch):
            raise RuntimeError("ClickHouse unavailable")

        source = BackfillSource("articles", ("id", "value"), ("id",), fail)
        importer = AnalyticsBackfill(ch, "initial", [source])
        with pytest.raises(RuntimeError, match="unavailable"):
            await importer.scan_one(pg, "articles")
        assert not ch.progress
        ch.progress[("initial", "articles")] = {
            "cursor": '"not-a-list"',
            "page": 1,
            "complete": 0,
            "boundary": "",
        }
        with pytest.raises(AnalyticsUnavailable, match="cursor"):
            await importer.scan_one(pg, "articles")

    asyncio.run(run())


def test_invalid_sources_are_rejected():
    async def write(_batch):
        pass

    with pytest.raises(ValueError):
        BackfillSource("articles; DROP TABLE articles", ("id",), ("id",), write)
    source = BackfillSource("articles", ("id",), ("id",), write)
    with pytest.raises(ValueError):
        AnalyticsBackfill(None, "initial", [source], page_size=1001)
    with pytest.raises(ValueError):
        AnalyticsBackfill(None, "initial", [source, source])


def test_import_boundary_is_immutable_across_restart():
    async def run():
        class Days(Postgres):
            async def execute(self, _statement, params=None):
                if params is None:
                    return None
                boundary = params[0]
                cursor = params[1] if len(params) == 3 else None
                return Cursor(
                    [
                        row
                        for row in self.records
                        if row["day"] < boundary and (cursor is None or row["day"] > cursor)
                    ][: params[-1]]
                )

        source = Days([{"day": "2026-01-01", "value": 1}, {"day": "2026-01-02", "value": 2}])
        sink = ClickHouse(source)
        values = []

        async def write(rows):
            values.extend(rows)

        spec = BackfillSource(
            "overview_daily", ("day", "value"), ("day",), write, before=("day", "2026-01-02")
        )
        importer = AnalyticsBackfill(sink, "initial", [spec])
        assert await importer.scan_one(source, "overview_daily")
        assert values == [{"day": "2026-01-01", "value": 1}]
        changed = BackfillSource(
            "overview_daily", ("day", "value"), ("day",), write, before=("day", "2026-01-03")
        )
        with pytest.raises(AnalyticsUnavailable, match="checkpoint"):
            await AnalyticsBackfill(sink, "initial", [changed]).scan_one(source, "overview_daily")

    asyncio.run(run())


def test_full_source_page_bounds_hydrated_publications_and_feeds(monkeypatch):
    """Hydration can add a second observation for every scanned business row."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from devfeed_core import analytics_producer
    from devfeed_core.analytics_import import HistoricalAnalyticsImport

    source_rows = [{"id": str(uuid.UUID(int=i))} for i in range(1, 1001)]
    hydrated = [{"source_table": "articles", "publication": row} for row in source_rows] * 2
    hydrate = AsyncMock(return_value=hydrated)
    monkeypatch.setattr(analytics_producer, "hydrate_changes", hydrate)
    importer = HistoricalAnalyticsImport.__new__(HistoricalAnalyticsImport)
    importer._connection = object()
    importer.overview = SimpleNamespace(publish=AsyncMock())
    asyncio.run(importer.import_source("articles", source_rows))
    hydrate.assert_awaited_once()
    assert [len(call.args[0]) for call in importer.overview.publish.await_args_list] == [1000, 1000]
    assert all(call.kwargs == {"revision": 1} for call in importer.overview.publish.await_args_list)
