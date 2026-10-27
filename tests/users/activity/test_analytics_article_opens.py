"""Direct article opens are idempotent across retry and historical import."""

import asyncio
import os
import struct
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.analytics_article_opens import ArticleOpenAnalytics
from devfeed_core.config import Settings


class RecordingClient:
    def __init__(self):
        self.inserts = []
        self.queries = []
        self.externals = []

    async def insert(self, table, rows):
        self.inserts.append((table, rows))

    async def query(self, statement, parameters, *, external_data=None):
        self.queries.append((statement, parameters))
        self.externals.append(external_data)
        return []


class CutoverClient:
    def __init__(self):
        self.rows = []

    async def insert(self, table, rows):
        assert table == "analytics_article_open_cutover"
        self.rows.extend(rows)

    async def query(self, _statement):
        if not self.rows:
            return [{"entries": 0}]
        return [
            {
                "entries": len(self.rows),
                "earliest": min(row["cutover_hour"] for row in self.rows).isoformat(),
                "latest": max(row["cutover_hour"] for row in self.rows).isoformat(),
                "ready": max(row["ready"] for row in self.rows),
            }
        ]


def test_cutover_requires_both_completed_imports_and_rejects_conflicts():
    async def run():
        client = CutoverClient()
        analytics = ArticleOpenAnalytics(client)
        stamp = datetime(2026, 2, 3, 12, 39, tzinfo=UTC)

        class Importer:
            complete_sources = False

            async def complete(self, sources):
                assert sources == ["article_engagement", "article_opens"]
                return self.complete_sources

        importer = Importer()
        with pytest.raises(AnalyticsUnavailable):
            await analytics.cutover()
        await analytics.begin_import(stamp)
        await analytics.begin_import(stamp)
        with pytest.raises(AnalyticsUnavailable, match="still running"):
            await analytics.mark_ready(stamp, importer)
        with pytest.raises(AnalyticsUnavailable, match="Conflicting"):
            await analytics.begin_import(stamp + timedelta(hours=1))
        importer.complete_sources = True
        assert await analytics.mark_ready(stamp, importer) == stamp.replace(minute=0)
        assert await analytics.cutover() == stamp.replace(minute=0)
        assert len(client.rows) == 2

    asyncio.run(run())


def test_valid_open_and_import_are_bounded_and_canonical():
    async def run():
        client = RecordingClient()
        analytics = ArticleOpenAnalytics(client)
        article = uuid.uuid4()
        stamp = datetime(2026, 2, 3, 12, 39, tzinfo=UTC)
        await analytics.record(article, "a" * 64, stamp)
        await analytics.import_opens(
            [{"article_id": article, "viewer_key": "a" * 64, "opened_hour": stamp}]
        )
        await analytics.import_totals([{"article_id": article, "opens": 7}])
        live, historic = client.inserts[0][1][0], client.inserts[1][1][0]
        assert live["opened_hour"] == datetime(2026, 2, 3, 12, tzinfo=UTC)
        assert historic == {**live, "historical": 1}
        assert client.inserts[2][1] == [{"article_id": str(article), "opens": 7}]
        assert await analytics.totals([article], cutover=stamp) == {str(article): 0}
        assert "analytics_open_lifetime" in client.queries[0][0]
        assert client.queries[0][1]["ids"] == [str(article)]

    asyncio.run(run())


def test_invalid_open_or_import_never_writes_data():
    async def run():
        client = RecordingClient()
        analytics = ArticleOpenAnalytics(client)
        article = uuid.uuid4()
        now = datetime.now(UTC)
        for viewer, when in (("bad", now), ("a" * 64, datetime.now()), ("A" * 64, now)):
            with pytest.raises(ValueError):
                await analytics.record(article, viewer, when)
        with pytest.raises(ValueError):
            await analytics.import_totals([{"article_id": article, "opens": -1}])
        with pytest.raises(ValueError):
            await analytics.import_opens([{"article_id": article}] * 1001)
        with pytest.raises(ValueError):
            await analytics.totals([article] * 101, cutover=now)
        with pytest.raises(ValueError):
            await analytics.recent_page(now, limit=1001)
        assert client.inserts == [] and client.queries == []

    asyncio.run(run())


@pytest.mark.integration
def test_real_clickhouse_deduplicates_opens_across_cutover_and_import_retries():
    url = os.environ.get("DEVFEED_TEST_CLICKHOUSE_URL")
    if not url:
        pytest.skip("Set DEVFEED_TEST_CLICKHOUSE_URL for ClickHouse integration")
    database = os.environ.get("DEVFEED_TEST_CLICKHOUSE_DATABASE", "devfeed_test")
    if not database.endswith("_test"):
        pytest.fail("Disposable ClickHouse database name must end with _test")
    settings = Settings(
        _env_file=None,
        database_url=os.environ["DEVFEED_TEST_DATABASE_URL"],
        redis_url=os.environ["DEVFEED_TEST_REDIS_URL"],
        clickhouse_url=url,
        clickhouse_database=database,
        clickhouse_username=os.environ.get("DEVFEED_TEST_CLICKHOUSE_USERNAME", "default"),
        clickhouse_password=os.environ.get("DEVFEED_TEST_CLICKHOUSE_PASSWORD", ""),
    )

    async def run():
        target = f"opens_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(
            settings.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": target})
                ) as client:
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    analytics = ArticleOpenAnalytics(client)
                    await analytics.initialize()
                    article, other = uuid.uuid4(), uuid.uuid4()
                    cutover = datetime(2026, 10, 26, 11, 30, tzinfo=UTC)
                    await analytics.record(article, "a" * 64, cutover)
                    await analytics.record(article, "b" * 64, cutover)
                    await analytics.record(article, "b" * 64, cutover)
                    await analytics.record(other, "c" * 64, cutover + timedelta(hours=1))
                    baseline = [{"article_id": article, "opens": 5}]
                    history = [
                        {"article_id": article, "viewer_key": "a" * 64, "opened_hour": cutover}
                    ]
                    await analytics.import_totals(baseline)
                    await analytics.import_totals(baseline)
                    await analytics.import_opens(history)
                    await analytics.import_opens(history)
                    totals = await analytics.totals([article, other], cutover=cutover)
                    assert totals == {str(article): 6, str(other): 1}
                    page = await analytics.recent_page(cutover.replace(minute=0))
                    assert {r["article_id"]: r["opens"] for r in page} == {
                        str(article): 2,
                        str(other): 1,
                    }
                    first = await analytics.recent_page(cutover.replace(minute=0), limit=1)
                    assert len(first) == 1
                    last = await analytics.recent_page(
                        cutover.replace(minute=0), after=first[0]["article_id"], limit=1
                    )
                    assert len(last) == 1 and last[0]["article_id"] != first[0]["article_id"]

                    # One server-side ranking query combines authoritative
                    # visibility and likes without a Python deep-offset heap.
                    epoch = datetime(1970, 1, 1, tzinfo=UTC)
                    micros = int((cutover - epoch).total_seconds() * 1_000_000)
                    metadata = b"".join(
                        struct.pack("<QQqQ", item.int >> 64, item.int & (2**64 - 1), micros, likes)
                        for item, likes in ((article, 0), (other, 1))
                    )
                    ranked = await analytics.trending(
                        cutover.replace(minute=0), metadata, limit=1, cursor=0
                    )
                    assert [row["article_id"] for row in ranked] == [str(other), str(article)]
                    deep = await analytics.trending(
                        cutover.replace(minute=0), metadata, limit=1, cursor=1
                    )
                    assert [row["article_id"] for row in deep] == [str(article)]

                    # Raw reusable viewer keys expire while idempotent exact
                    # lifetime states and reader sketches remain queryable.
                    retired = uuid.uuid4()
                    old = datetime.now(UTC) - timedelta(days=40)
                    await analytics.record(retired, "d" * 64, old)
                    await analytics.record(retired, "d" * 64, old)
                    await client.execute("OPTIMIZE TABLE analytics_article_opens FINAL")
                    raw = await client.query(
                        "SELECT count() AS n FROM analytics_article_opens "
                        "WHERE article_id={id:UUID}",
                        {"id": str(retired)},
                    )
                    assert raw == [{"n": 0}]
                    assert await analytics.totals([retired], cutover=old) == {str(retired): 1}
                    audience = await client.query(
                        "SELECT uniqExactMerge(open_state) AS opens,"
                        "uniqCombined64Merge(reader_state) AS readers "
                        "FROM analytics_open_audience_hourly "
                        "WHERE bucket_hour<now()-INTERVAL 30 DAY"
                    )
                    assert audience == [{"opens": 1, "readers": 1}]
                    await analytics.import_totals([{"article_id": retired, "opens": 5}])
                    await analytics.import_opens(
                        [{"article_id": retired, "viewer_key": "d" * 64, "opened_hour": old}]
                    )
                    await client.execute("OPTIMIZE TABLE analytics_article_opens FINAL")
                    assert await analytics.totals([retired], cutover=old) == {str(retired): 5}

                    # A failed materialized-view insert is retried through the
                    # raw table; replay cannot inflate either aggregate target.
                    failed = uuid.uuid4()
                    await client.execute(
                        "CREATE TABLE analytics_open_failure (v UInt8) ENGINE=Null"
                    )
                    await client.execute(
                        "CREATE MATERIALIZED VIEW analytics_open_failure_ingest "
                        "TO analytics_open_failure AS SELECT toUInt8(throwIf(article_id="
                        f"toUUID('{failed}'))) AS v FROM analytics_article_opens"
                    )
                    with pytest.raises(AnalyticsUnavailable):
                        await analytics.record(failed, "e" * 64, cutover)
                    await client.execute("DROP VIEW analytics_open_failure_ingest")
                    await analytics.record(failed, "e" * 64, cutover)
                    assert await analytics.totals([failed], cutover=cutover) == {str(failed): 1}
            finally:
                await admin.execute(f"DROP DATABASE IF EXISTS `{target}`")

    asyncio.run(run())


def test_recent_pages_use_the_sorted_article_identity_for_keyset_paging():
    async def run():
        client = RecordingClient()
        analytics = ArticleOpenAnalytics(client)
        identity = str(uuid.uuid4())
        await analytics.recent_page(datetime.now(UTC), after=identity, limit=10)
        statement, parameters = client.queries[0]
        assert "article_key>{after:String}" in statement
        assert "GROUP BY article_key ORDER BY article_key" in statement
        assert parameters["after"] == identity

    asyncio.run(run())


def test_raw_viewer_retention_has_idempotent_lifetime_and_reader_states():
    from devfeed_core.analytics_article_opens import (
        AUDIENCE_VIEW_DDL,
        LIFETIME_VIEW_DDL,
        OPEN_DDL,
    )

    assert "INTERVAL 30 DAY DELETE" in OPEN_DDL
    assert "uniqExactState(event_id)" in LIFETIME_VIEW_DDL
    assert "uniqExactStateIf(event_id,historical=1)" in LIFETIME_VIEW_DDL
    assert "uniqCombined64State(toString(viewer_key))" in AUDIENCE_VIEW_DDL


def test_trending_passes_bounded_external_business_data_to_one_query():
    async def run():
        client = RecordingClient()
        analytics = ArticleOpenAnalytics(client)
        identity = uuid.uuid4().int
        payload = struct.pack("<QQqQ", identity >> 64, identity & (2**64 - 1), 1700000000000000, 2)
        await analytics.trending(datetime.now(UTC), payload, limit=2, cursor=900000)
        assert len(client.queries) == 1
        statement, params = client.queries[0]
        assert "OFFSET {offset:UInt32}" in statement
        assert params["offset"] == 900000 and params["limit"] == 3
        external = client.externals[0]
        assert external.files[0].data == payload
        assert "visible_articles" in statement

    asyncio.run(run())
