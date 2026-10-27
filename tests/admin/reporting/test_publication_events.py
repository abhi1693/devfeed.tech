"""Signed publication events retain attribution across moves and safe retries."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_publication_events import Attribution, Publication, PublicationEvents
from devfeed_core.config import Settings


class Sink:
    def __init__(self):
        self.batches = []

    async def insert(self, table, rows):
        assert table == "analytics_publication_events"
        self.batches.append(rows)


def test_move_emits_both_owners_and_retry_reuses_event_ids():
    async def run():
        sink = Sink()
        store = PublicationEvents(sink)
        article_a, article_b, relation, target = [uuid.uuid4() for _ in range(4)]
        old = Attribution("source", str(article_a), str(relation), str(target))
        new = Attribution("source", str(article_b), str(relation), str(target))
        mutation = uuid.uuid4()
        assert await store.change(mutation, old_links=[old], new_links=[new]) == 2
        assert await store.change(mutation, old_links=[old], new_links=[new]) == 2
        left, right = sink.batches[0]
        assert (left["article_id"], left["delta"]) == (str(article_a), -1)
        assert (right["article_id"], right["delta"]) == (str(article_b), 1)
        assert sink.batches[0] == sink.batches[1]
        assert left["event_id"] != right["event_id"]

    asyncio.run(run())


def test_unpublish_retains_history_and_delete_retracts_it():
    async def run():
        sink = Sink()
        store = PublicationEvents(sink)
        publication = Publication(str(uuid.uuid4()), datetime(2026, 1, 1, tzinfo=UTC))
        assert await store.change(uuid.uuid4(), before=publication, after=publication) == 0
        assert not sink.batches
        assert await store.change(uuid.uuid4(), before=publication) == 1
        assert sink.batches[0][0]["delta"] == -1

    asyncio.run(run())


def test_topic_composite_identity_and_role_filter_are_stable():
    article, topic = uuid.uuid4(), uuid.uuid4()
    primary = Attribution.topic(article, topic, "primary")
    supporting = Attribution.topic(article, topic, "supporting")
    assert primary == supporting and primary is not None
    assert Attribution.topic(article, topic, "comparison") is None
    assert Attribution.topic(article, topic, "incidental") is None
    with pytest.raises(ValueError):
        Attribution.topic(article, topic, "unknown")


def test_invalid_or_oversized_mutations_fail_before_insert():
    async def run():
        sink = Sink()
        store = PublicationEvents(sink)
        with pytest.raises(ValueError):
            await store.change("bad-id")
        with pytest.raises(ValueError):
            Publication(str(uuid.uuid4()), datetime(2026, 1, 1))
        with pytest.raises(ValueError):
            Attribution("unapproved", str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
        link = Attribution("source", str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
        with pytest.raises(ValueError):
            await store.change(uuid.uuid4(), old_links=[link] * 501)
        assert not sink.batches

    asyncio.run(run())


@pytest.mark.integration
def test_clickhouse_events_are_order_independent_and_count_distinct_origins():
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
        target = f"publication_events_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(
            settings.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": target})
                ) as client:
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    store = PublicationEvents(client)
                    await store.initialize()
                    start = datetime(2026, 1, 1, tzinfo=UTC)
                    end = start + timedelta(days=3)
                    a, b, source, topic, origin1, origin2, topic_link = [
                        str(uuid.uuid4()) for _ in range(7)
                    ]
                    pa = Publication(a, start + timedelta(hours=1), start, True)
                    pb = Publication(b, start + timedelta(hours=2), start, False)
                    o1 = Attribution("source", a, origin1, source)
                    o2 = Attribution("source", a, origin2, source)
                    ot = Attribution("topic", a, topic_link, topic)
                    moved_o = Attribution("source", b, origin2, source)
                    moved_t = Attribution("topic", b, topic_link, topic)
                    # Applying a delete before the corresponding add still converges.
                    await store.change(uuid.uuid4(), old_links=[o1])
                    first = uuid.uuid4()
                    await store.change(first, after=pa, new_links=[o1, o2, ot])
                    await store.change(first, after=pa, new_links=[o1, o2, ot])
                    assert await store.attribution(start, end, kind="source") == [
                        {"id": source, "publications": 1}
                    ]
                    await store.change(
                        uuid.uuid4(), old_links=[o2, ot], new_links=[moved_o, moved_t]
                    )
                    await store.change(uuid.uuid4(), after=pb)
                    assert await store.attribution(start, end, kind="source") == [
                        {"id": source, "publications": 1}
                    ]
                    assert await store.attribution(start, end, kind="topic") == [
                        {"id": topic, "publications": 1}
                    ]
                    assert await store.change(uuid.uuid4(), before=pa, after=pa) == 0
                    rows = await store.overview(start, end)
                    assert [r for r in rows if int(r["total"]) == 1][0]["published"] == 2
                    await store.change(uuid.uuid4(), before=pa)
                    rows = await store.overview(start, end)
                    assert [r for r in rows if int(r["total"]) == 1][0]["published"] == 1
                    moved_date = Publication(b, start + timedelta(days=1), start, False)
                    await store.change(uuid.uuid4(), before=pb, after=moved_date)
                    rows = await store.overview(start, start + timedelta(days=1))
                    assert sum(r["published"] for r in rows if int(r["total"]) == 1) == 0
                    assert await store.attribution(start, end, kind="source") == [
                        {"id": source, "publications": 1}
                    ]
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())
