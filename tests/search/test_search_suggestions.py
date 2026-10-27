import asyncio

import pytest
from devfeed_core.models import SearchQueryStat
from devfeed_core.search_suggestions import (
    SearchSuggestionAnalytics,
    normalize_query,
    query_hash,
    record_successful_query,
    review_query,
)

pytestmark = pytest.mark.integration


class ClickHouse:
    async def query(self, _statement, _parameters):
        return [{"complete": 1}]


class Counts:
    def __init__(self, values):
        self.values = values

    async def search_counts(self, identities):
        return [
            {
                "query_hash": item,
                "successful_count": self.values[item],
                "first_seen": "2026-01-01T00:00:00Z",
                "last_seen": "2026-01-02T00:00:00Z",
            }
            for item in identities
            if item in self.values
        ]


def test_successful_query_metadata_is_normalized_without_pg_analytics_count(database):
    with database.begin() as session:
        first = record_successful_query(session, "  Kubernetes   Networking ")
        second = record_successful_query(session, "kubernetes networking")
        row = session.get(SearchQueryStat, first)
        assert first == second == query_hash("kubernetes networking")
        assert row.query == "kubernetes networking"
        assert not {"successful_count", "first_seen_at", "last_seen_at"} & set(
            SearchQueryStat.__table__.columns.keys()
        )
        assert row.reviewed_by is None


def test_only_approved_queries_above_clickhouse_volume_threshold_are_suggested(database):
    with database.begin() as session:
        approved = record_successful_query(session, "kubernetes")
        record_successful_query(session, "python")
        review_query(session, approved, status="approved", reviewer="admin", note="safe")
        pending = record_successful_query(session, "kubernetes networking")
        review_query(session, pending, status="approved", reviewer="admin", note=None)
    analytics = SearchSuggestionAnalytics(ClickHouse())
    analytics.store = Counts({approved: 2, pending: 1})
    with database() as session:
        rows = asyncio.run(
            analytics.rank(database, prefix="kube", status="approved", limit=5, minimum=2)
        )
    assert [item["query"] for item in rows] == ["kubernetes"]


def test_rejected_query_remains_hidden_and_query_validation_is_bounded(database):
    with database.begin() as session:
        digest = record_successful_query(session, "unsafe suggestion")
        review_query(session, digest, status="rejected", reviewer="admin", note="not useful")
    analytics = SearchSuggestionAnalytics(ClickHouse())
    analytics.store = Counts({digest: 5})
    with database() as session:
        rows = asyncio.run(
            analytics.rank(database, prefix="unsafe", status="approved", limit=5, minimum=1)
        )
    assert rows == []
    assert normalize_query("  Python\tAsync ") == "python async"
    for value in ["", "!!!", "word " * 21]:
        with pytest.raises(ValueError):
            normalize_query(value)


def test_clickhouse_counts_drive_rank_across_postgres_pages(database, integration_environment):
    import os
    import uuid
    from datetime import UTC, datetime

    from devfeed_core.analytics import AnalyticsClient
    from devfeed_core.analytics_backfill import BACKFILL_DDL
    from devfeed_core.analytics_direct_engagement import DirectEngagementAnalytics
    from devfeed_core.config import get_settings
    from pydantic import SecretStr

    url = os.environ.get("DEVFEED_TEST_CLICKHOUSE_URL")
    if not url:
        pytest.skip("Set DEVFEED_TEST_CLICKHOUSE_URL for ClickHouse integration")
    ch_database = os.environ.get("DEVFEED_TEST_CLICKHOUSE_DATABASE", "devfeed_test")
    if not ch_database.endswith("_test"):
        pytest.fail("Disposable ClickHouse database name must end with _test")
    target = f"search_{uuid.uuid4().hex}_test"
    settings = get_settings().model_copy(
        update={
            "clickhouse_url": url,
            "clickhouse_username": os.environ.get("DEVFEED_TEST_CLICKHOUSE_USERNAME", "default"),
            "clickhouse_password": SecretStr(
                os.environ.get("DEVFEED_TEST_CLICKHOUSE_PASSWORD", "")
            ),
            "clickhouse_database": "default",
        }
    )
    items = [{"query_hash": f"{i:064x}", "query": f"search {i:04d}"} for i in range(501)]
    with database.begin() as session:
        session.add_all([SearchQueryStat(**item, status="approved") for item in items])

    async def run():
        async with AnalyticsClient(settings) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": target})
                ) as client:
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    await client.execute(BACKFILL_DDL)
                    await client.insert(
                        "analytics_backfill_progress",
                        [
                            {
                                "import_id": "direct-v1",
                                "source": "search_query_stats",
                                "page": 1,
                                "cursor": "",
                                "complete": 1,
                            }
                        ],
                    )
                    store = DirectEngagementAnalytics(client)
                    await store.initialize()
                    now = datetime(2026, 1, 1, tzinfo=UTC)
                    await store.search_events(
                        [
                            {
                                "query_hash": item["query_hash"],
                                "event_id": uuid.uuid4(),
                                "occurred_at": now,
                            }
                            for item in items
                        ]
                    )
                    await store.search_events(
                        [
                            {
                                "query_hash": items[-1]["query_hash"],
                                "event_id": uuid.uuid4(),
                                "occurred_at": now,
                            }
                            for _ in range(3)
                        ]
                    )
                    result = await SearchSuggestionAnalytics(client).rank(
                        database, prefix="search", status="approved", limit=2, minimum=1
                    )
                    assert [(row["query"], row["successful_count"]) for row in result] == [
                        ("search 0500", 4),
                        ("search 0000", 1),
                    ]
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())
