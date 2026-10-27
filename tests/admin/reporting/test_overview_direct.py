"""Direct observations keep chart state in ClickHouse and evaluate current deadlines."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_overview_direct import DirectOverviewAnalytics
from devfeed_core.config import Settings


class Sink:
    def __init__(self):
        self.rows = []

    async def insert(self, table, rows):
        assert table == "analytics_overview_events"
        self.rows.extend(rows)


def test_writes_only_safe_fields_and_retains_every_job_attempt():
    async def run():
        sink = Sink()
        store = DirectOverviewAnalytics(sink)
        job = uuid.uuid4()
        at = datetime(2026, 1, 1, tzinfo=UTC)
        await store.jobs(
            [
                {
                    "id": job,
                    "kind": "analysis",
                    "status": "failed",
                    "revision": 1,
                    "attempt": 1,
                    "finished_at": at,
                    "prompt": "private",
                },
                {
                    "id": job,
                    "kind": "analysis",
                    "status": "succeeded",
                    "revision": 2,
                    "attempt": 2,
                    "finished_at": at,
                    "outcome": "applied",
                    "prompt": "private",
                },
            ]
        )
        current = [row for row in sink.rows if row["domain"] == "queue"]
        attempts = [row for row in sink.rows if row["domain"] == "job"]
        assert len(attempts) == 2 and attempts[0]["id"] != attempts[1]["id"]
        assert len(current) == 2 and current[0]["id"] == current[1]["id"]
        assert "private" not in str(sink.rows)

    asyncio.run(run())


def test_invalid_values_rejected_before_insert():
    async def run():
        sink = Sink()
        store = DirectOverviewAnalytics(sink)
        with pytest.raises(ValueError):
            await store.jobs(
                [{"id": uuid.uuid4(), "kind": "unexpected", "status": "queued", "revision": 1}]
            )
        with pytest.raises(ValueError):
            await store.feeds([{"user_id": uuid.uuid4(), "revision": -1}])
        with pytest.raises(ValueError):
            await store.publications(
                [{"article_id": uuid.uuid4(), "revision": 1, "published_at": datetime(2026, 1, 1)}]
            )
        assert sink.rows == []

    asyncio.run(run())


@pytest.mark.integration
def test_direct_charts_handle_reparent_retry_and_feed_expiration():
    url = os.environ.get("DEVFEED_TEST_CLICKHOUSE_URL")
    if not url:
        pytest.skip("Set DEVFEED_TEST_CLICKHOUSE_URL for ClickHouse integration")
    database = os.environ.get("DEVFEED_TEST_CLICKHOUSE_DATABASE", "devfeed_test")
    if not database.endswith("_test"):
        pytest.fail("Disposable ClickHouse database name must end with _test")
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://fixture@database.invalid/fixture_test",
        redis_url="redis://redis.invalid/15",
        clickhouse_url=url,
        clickhouse_database=database,
        clickhouse_username=os.environ.get("DEVFEED_TEST_CLICKHOUSE_USERNAME", "default"),
        clickhouse_password=os.environ.get("DEVFEED_TEST_CLICKHOUSE_PASSWORD", ""),
    )

    async def run():
        target = f"overview_direct_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(
            settings.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": target})
                ) as client:
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    store = DirectOverviewAnalytics(client)
                    await store.initialize()
                    start = datetime(2026, 1, 1, tzinfo=UTC)
                    end = start + timedelta(days=2)
                    a, b, source, topic, job, user = [uuid.uuid4() for _ in range(6)]
                    base = {
                        "published_at": start + timedelta(hours=1),
                        "discovered_at": start,
                        "source_ids": [source],
                        "topic_ids": [topic],
                        "automated": True,
                    }
                    await store.publications(
                        [
                            {**base, "article_id": a, "revision": 1},
                            {
                                **base,
                                "article_id": b,
                                "revision": 1,
                                "source_ids": [],
                                "topic_ids": [],
                            },
                        ]
                    )
                    # An association moves from A to B. Both complete owner snapshots change.
                    changes = [
                        {**base, "article_id": a, "revision": 2, "source_ids": [], "topic_ids": []},
                        {**base, "article_id": b, "revision": 2},
                    ]
                    await store.publications(changes)
                    await store.publications(changes)
                    assert (await store.publication_dimensions(start, end, dimension="source")) == [
                        {"id": str(source), "publications": 1}
                    ]
                    assert (await store.publication_dimensions(start, end, dimension="topic")) == [
                        {"id": str(topic), "publications": 1}
                    ]
                    pubs = await store.publication_report(start, end)
                    assert pubs[0]["published"] == 2 and pubs[0]["automated"] == 2
                    overview = await store.publication_overview(start, end)
                    assert len(overview) == 2
                    assert {int(row["total"]) for row in overview} == {0, 1}
                    assert all(row["published"] == 2 for row in overview)
                    await store.jobs(
                        [
                            {
                                "id": job,
                                "kind": "analysis",
                                "status": "failed",
                                "revision": 1,
                                "attempt": 1,
                                "created_at": start,
                                "finished_at": start,
                                "duration_ms": 2000,
                            },
                            {
                                "id": job,
                                "kind": "analysis",
                                "status": "succeeded",
                                "revision": 2,
                                "attempt": 2,
                                "created_at": start,
                                "finished_at": start,
                                "duration_ms": 4000,
                                "outcome": "applied",
                            },
                        ]
                    )
                    jobs = await store.job_outcomes(start, end)
                    assert jobs[0]["completed"] == jobs[0]["failed"] == 1
                    await store.decisions(
                        [
                            {
                                "id": uuid.uuid4(),
                                "revision": 1,
                                "status": "approved",
                                "reviewed_at": start,
                            },
                            {
                                "id": uuid.uuid4(),
                                "revision": 1,
                                "status": "rejected",
                                "reviewed_at": start,
                            },
                        ]
                    )
                    throughput = await store.throughput(start, end)
                    assert throughput[0]["article_analyses"] == 1
                    assert throughput[0]["topic_decisions"] == 2
                    assert await store.queues() == []
                    await store.feeds(
                        [
                            {
                                "user_id": user,
                                "revision": 1,
                                "interest_count": 1,
                                "has_generation": True,
                                "expires_at": start + timedelta(hours=4),
                                "next_refresh_at": start + timedelta(hours=1),
                            }
                        ]
                    )
                    now = start + timedelta(hours=2)
                    health = (await store.feed_health(now))[0]
                    assert (
                        health["ready"] == 1 and health["overdue"] == 1 and health["expired"] == 0
                    )
                    health = (await store.feed_health(start + timedelta(hours=5)))[0]
                    assert health["ready"] == 0 and health["expired"] == 1
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())


def test_decisions_include_rejections_and_reject_unreviewed_states():
    async def run():
        sink = Sink()
        store = DirectOverviewAnalytics(sink)
        at = datetime(2026, 1, 1, tzinfo=UTC)
        identity = uuid.uuid4()
        await store.decisions(
            [
                {"id": identity, "revision": 1, "status": "approved", "reviewed_at": at},
                {"id": uuid.uuid4(), "revision": 1, "status": "rejected", "reviewed_at": at},
            ]
        )
        assert [row["outcome"] for row in sink.rows] == ["approved", "rejected"]
        assert all(
            row["task"] == "topic-decision" and row["status"] == "succeeded" for row in sink.rows
        )
        with pytest.raises(ValueError, match="topic decision"):
            await store.decisions(
                [{"id": identity, "revision": 2, "status": "pending", "reviewed_at": at}]
            )
        assert len(sink.rows) == 2
        await store.decisions([{"id": identity, "revision": 3, "deleted": True}])
        assert sink.rows[-1]["id"] == sink.rows[0]["id"] and sink.rows[-1]["deleted"] == 1

    asyncio.run(run())
