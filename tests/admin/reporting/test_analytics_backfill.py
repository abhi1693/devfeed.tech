"""Historical pages can be retried without checkpointing incomplete work."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

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


@pytest.fixture
def historical_import():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from devfeed_core.analytics_import import HistoricalAnalyticsImport

    client = SimpleNamespace(execute=AsyncMock(), insert=AsyncMock(), query=AsyncMock())
    importer = HistoricalAnalyticsImport(client, cutover=datetime(2026, 10, 1, tzinfo=UTC))
    for store in (importer.opens, importer.engagement, importer.inference, importer.overview):
        store.initialize = AsyncMock()
    importer.opens.begin_import = AsyncMock()
    importer.opens.mark_ready = AsyncMock()
    return importer


@pytest.mark.parametrize("cutover", [None, "2026-10-01", datetime(2026, 10, 1)])
def test_historical_import_requires_aware_fixed_cutover(cutover):
    from devfeed_core.analytics_import import HistoricalAnalyticsImport

    with pytest.raises(ValueError, match="timezone-aware"):
        HistoricalAnalyticsImport(None, cutover=cutover)


def test_historical_import_bounds_cards_and_normalizes_source_cutoff():
    from datetime import UTC, datetime, timedelta, timezone

    from devfeed_core.analytics_import import HistoricalAnalyticsImport

    imported = HistoricalAnalyticsImport(
        None, cutover=datetime(2026, 10, 1, 9, 45, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    )
    assert imported.cutover == datetime(2026, 10, 1, 4, tzinfo=UTC)
    assert imported.cards_importer.page_size == 200
    assert imported.importer.sources["overview_daily"].before == ("day", "2026-10-01")
    assert imported.importer.sources["topic_decision_calls"].keys == ("proposal_id", "ordinal")


@pytest.mark.parametrize("dictionary_rows", [False, True])
def test_historical_source_discovery_accepts_absent_retired_tables_only(
    historical_import, dictionary_rows
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    importer = historical_import
    rows = [
        (name, field)
        for name, source in importer.importer.sources.items()
        for field in source.fields
        if name != "inference_calls"
        and not (name == "search_query_stats" and field == "successful_count")
    ]
    if dictionary_rows:
        rows = [dict(table_name=name, column_name=field) for name, field in rows]
    connection = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(fetchall=AsyncMock(return_value=rows)))
    )
    asyncio.run(importer.configure(connection))
    assert set(importer.absent) == {"inference_calls", "search_query_stats"}
    rows = [
        (name, field)
        for name, source in importer.importer.sources.items()
        for field in source.fields
        if name != "articles"
    ]
    connection.execute.return_value.fetchall.return_value = rows
    with pytest.raises(RuntimeError, match="source schema is incomplete: articles"):
        asyncio.run(importer.configure(connection))
    importer.client.execute.assert_not_called()


def test_absent_history_is_checkpointed_but_completed_sources_are_preserved(historical_import):
    from unittest.mock import AsyncMock

    importer = historical_import
    importer.absent = ["article_opens", "overview_daily", "inference_calls"]
    importer.importer._progress = AsyncMock(
        side_effect=[(None, 0, False), (None, 0, False), (["retained"], 2, True)]
    )
    asyncio.run(importer.initialize())
    rows = [call.args[1][0] for call in importer.client.insert.await_args_list]
    assert [row["source"] for row in rows] == ["article_opens", "overview_daily"]
    assert [row["boundary"] for row in rows] == ["", "2026-10-01"]
    assert all(row["complete"] == 1 for row in rows)
    importer.opens.begin_import.assert_awaited_once_with(importer.cutover)


@pytest.mark.parametrize("progress", [(["lost"], 0, False), (None, 1, False)])
def test_missing_source_with_partial_progress_fails_before_readiness(historical_import, progress):
    from unittest.mock import AsyncMock

    importer = historical_import
    importer.absent = ["article_opens"]
    importer.importer._progress = AsyncMock(return_value=progress)
    with pytest.raises(RuntimeError, match="disappeared before completion"):
        asyncio.run(importer.initialize())
    importer.client.insert.assert_not_called()
    importer.opens.begin_import.assert_not_called()


def test_source_page_connection_is_exclusive_and_released_after_failure(historical_import):
    from unittest.mock import AsyncMock

    importer, source = historical_import, object()

    async def failing_page(connection, name):
        assert connection is source and importer._connection is source
        assert name == "article_opens"
        with pytest.raises(RuntimeError, match="already processing"):
            await importer.scan_one(source, name)
        raise RuntimeError("failed target write")

    importer.importer.scan_one = AsyncMock(side_effect=failing_page)
    with pytest.raises(RuntimeError, match="failed target"):
        asyncio.run(importer.scan_one(source, "article_opens"))
    assert importer._connection is None
    importer.importer.scan_one = AsyncMock(return_value=True)
    assert asyncio.run(importer.scan_one(source, "article_opens"))
    assert importer._connection is None
    with pytest.raises(RuntimeError, match="source connection"):
        asyncio.run(importer.import_partner_claims([]))
    with pytest.raises(RuntimeError, match="source connection"):
        asyncio.run(importer.import_source("articles", []))


@pytest.mark.parametrize("opens_complete", [False, True])
def test_history_cycle_scans_one_page_and_only_exposes_complete_reader_totals(
    historical_import, opens_complete
):
    from unittest.mock import AsyncMock

    importer, connection = historical_import, object()
    importer.importer.complete = AsyncMock(side_effect=[False, opens_complete])
    importer.scan_one = AsyncMock(return_value=True)
    assert asyncio.run(importer.once(connection))
    importer.scan_one.assert_awaited_once_with(connection, "partner_delivery_events")
    assert importer.opens.mark_ready.await_count == int(opens_complete)


@pytest.mark.parametrize("cards_complete", [False, True])
def test_completed_history_waits_for_policy_card_pages_without_rescanning_sources(
    historical_import, cards_complete
):
    from unittest.mock import AsyncMock

    importer, connection = historical_import, object()
    importer.importer.complete = AsyncMock(return_value=True)
    importer.scan_one = AsyncMock()
    importer.cards_importer.sources = {"articles": importer.cards_importer.sources["articles"]}
    importer.cards_importer.complete = AsyncMock(return_value=cards_complete)
    importer.cards_importer.scan_one = AsyncMock(return_value=True)
    assert asyncio.run(importer.once(connection)) is not cards_complete
    importer.scan_one.assert_not_called()
    assert importer.cards_importer.scan_one.await_count == int(not cards_complete)
    importer.opens.mark_ready.assert_awaited_once_with(importer.cutover, importer.importer)


def test_partner_claims_preserve_cold_history_only_after_receipt_backfill(
    historical_import, monkeypatch
):
    from datetime import date
    from unittest.mock import AsyncMock

    from devfeed_core import analytics_import

    importer = historical_import
    importer._connection = object()
    receipt = dict(
        asset_id=uuid.uuid4(), delivery_id=uuid.uuid4(), kind="click", day=date(2026, 10, 1)
    )
    backfill = AsyncMock()
    monkeypatch.setattr(analytics_import, "backfill_partner_claims", backfill)
    asyncio.run(importer.import_partner_claims([receipt]))
    backfill.assert_awaited_once_with(importer._connection, [receipt])
    table, rows = importer.client.insert.await_args.args
    assert table == "analytics_history_archive"
    assert json.loads(rows[0]["identity"]) == [
        str(receipt["asset_id"]),
        str(receipt["delivery_id"]),
        "click",
    ]
    assert json.loads(rows[0]["record"])["day"] == "2026-10-01"
    importer.client.insert.reset_mock()
    backfill.side_effect = RuntimeError("receipt update failed")
    with pytest.raises(RuntimeError, match="receipt update failed"):
        asyncio.run(importer.import_partner_claims([receipt]))
    importer.client.insert.assert_not_called()


def test_policy_card_page_publishes_baseline_revision(historical_import, monkeypatch):
    from unittest.mock import AsyncMock

    from devfeed_core import analytics_direct_cards

    publish = AsyncMock()
    monkeypatch.setattr(analytics_direct_cards, "publish_card_ids", publish)
    rows = [{"id": uuid.uuid4()}, {"id": uuid.uuid4()}]
    asyncio.run(historical_import.import_cards("article", rows))
    publish.assert_awaited_once_with(
        historical_import.client,
        historical_import.settings,
        "article",
        [row["id"] for row in rows],
        1,
    )


def test_closed_audience_history_exports_only_valid_aggregate_fields(historical_import):
    from datetime import date

    rows = [
        dict(
            day="2026-09-01",
            metrics=dict(opens=12, readers=2.5, accounts=3, published=4, private="ignored"),
        ),
        dict(day=date(2026, 9, 2), metrics={}),
    ]
    asyncio.run(historical_import.import_overview(rows))
    table, written = historical_import.client.insert.await_args.args
    assert table == "analytics_audience_archive_daily"
    assert written[0] == dict(
        day=date(2026, 9, 1), opens=12, readers=2.5, accounts_created=3, published=4, version=2**63
    )
    assert written[1]["opens"] is None
    assert all("private" not in row for row in written)


@pytest.mark.parametrize(
    "metrics",
    [
        None,
        [],
        {"readers": -1},
        {"readers": float("inf")},
        {"readers": float("nan")},
        {"readers": "2"},
        {"opens": True},
        {"opens": -1},
        {"opens": 2**64},
    ],
)
def test_invalid_historical_totals_are_never_written(historical_import, metrics):
    with pytest.raises(ValueError, match="historical"):
        asyncio.run(historical_import.import_overview([dict(day="2026-09-01", metrics=metrics)]))
    historical_import.client.insert.assert_not_called()


def test_partner_and_search_baselines_keep_fixed_versions_and_utc_times(historical_import):
    from datetime import UTC, date, datetime
    from unittest.mock import AsyncMock

    importer = historical_import
    importer.engagement.partner_totals = AsyncMock()
    importer.engagement.search_baselines = AsyncMock()
    when = datetime(2026, 10, 1, tzinfo=UTC)
    rows = [
        dict(
            asset_id=uuid.uuid4(),
            day="2026-10-01",
            impressions=12,
            clicks=3,
            updated_at="2026-10-01T00:00:00Z",
        ),
        dict(
            asset_id=uuid.uuid4(), day=date(2026, 10, 1), impressions=4, clicks=0, updated_at=when
        ),
    ]
    asyncio.run(importer.import_partner_totals(rows))
    asyncio.run(importer.import_partner_baseline(rows))
    calls = importer.engagement.partner_totals.await_args_list
    assert [call.kwargs["baseline"] for call in calls] == [False, True]
    assert all(
        row["revision"] == 1 and row["updated_at"] == when and row["day"] == when.date()
        for row in calls[0].args[0]
    )
    asyncio.run(
        importer.import_search_baseline(
            [
                dict(
                    query_hash="a" * 64,
                    successful_count=7,
                    first_seen_at=when,
                    last_seen_at="2026-10-01T00:00:00Z",
                )
            ]
        )
    )
    assert importer.engagement.search_baselines.await_args.args[0] == [
        dict(query_hash="a" * 64, count=7, first_seen=when, last_seen=when, revision=1)
    ]


@pytest.mark.parametrize(
    "import_id,sources", [("not a key", None), ("initial", []), ("initial", [object()])]
)
def test_backfill_requires_stable_identity_and_allowlisted_sources(import_id, sources):
    with pytest.raises(ValueError):
        AnalyticsBackfill(None, import_id, sources)


def test_unknown_source_sets_and_ambiguous_checkpoints_never_scan_postgres():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    source = BackfillSource("articles", ("id",), ("id",), AsyncMock())
    client = SimpleNamespace(query=AsyncMock(return_value=[{}, {}]))
    importer = AnalyticsBackfill(client, "initial", [source])
    with pytest.raises(ValueError, match="source set"):
        asyncio.run(importer.complete([]))
    with pytest.raises(ValueError, match="Unknown historical"):
        asyncio.run(importer.scan_one(None, "not_allowed"))
    with pytest.raises(AnalyticsUnavailable, match="Ambiguous"):
        asyncio.run(importer.complete(["articles"]))


@pytest.mark.parametrize(
    "checkpoint",
    [
        dict(cursor="not-json", page=1, complete=0, boundary=""),
        dict(cursor="", page=0, complete=0, boundary=""),
        dict(cursor="", page=1, complete=2, boundary=""),
        dict(cursor="[null]", page=1, complete=0, boundary=""),
    ],
)
def test_corrupt_checkpoints_fail_before_source_reads(checkpoint):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    source = BackfillSource("articles", ("id",), ("id",), AsyncMock())
    client = SimpleNamespace(query=AsyncMock(return_value=[checkpoint]))
    importer = AnalyticsBackfill(client, "initial", [source])
    with pytest.raises(AnalyticsUnavailable, match="checkpoint|cursor"):
        asyncio.run(importer.scan_one(None, "articles"))


@pytest.mark.parametrize(
    "records", [[], [{"unexpected": 1}], [{"id": None}], [{"id": 1}, {"id": 2}]]
)
def test_source_page_shape_is_verified_before_writes_and_empty_page_finishes(records):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    @asynccontextmanager
    async def transaction():
        yield

    rows = SimpleNamespace(fetchall=AsyncMock(return_value=[{"record": row} for row in records]))
    connection = SimpleNamespace(transaction=transaction, execute=AsyncMock(return_value=rows))
    client = SimpleNamespace(query=AsyncMock(return_value=[]), insert=AsyncMock())
    write = AsyncMock()
    importer = AnalyticsBackfill(
        client, "initial", [BackfillSource("articles", ("id",), ("id",), write)], page_size=1
    )
    if records:
        with pytest.raises(AnalyticsUnavailable, match="Invalid historical import page"):
            asyncio.run(importer.scan_one(connection, "articles"))
        client.insert.assert_not_called()
    else:
        assert asyncio.run(importer.scan_one(connection, "articles"))
        assert client.insert.await_args.args[1][0]["complete"] == 1
    write.assert_not_called()
