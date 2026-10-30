"""Native source fences keep delayed reconciliation behind newer live writes."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_connections import postgres_connections
from devfeed_core.analytics_direct_cards import build_cards
from devfeed_core.analytics_observation import pipeline_policy_version
from devfeed_core.analytics_producer import DirectAnalyticsPublisher
from devfeed_core.analytics_reconcile import FINAL_GUARDS, PUBLISH_LOCK, CurrentStateReconciler
from devfeed_core.models import Article, ArticleEnrichmentJob, ArticleOrigin, ArticleReview
from sqlalchemy import delete

from tests.support import analytics, partner_matching, source_readiness


@pytest.mark.integration
def test_late_reconciliation_preserves_newer_live_queue_and_card_and_waits_for_a_busy_publisher(
    database,
):
    now = datetime.now(UTC)
    source = source_readiness.source(approval="approved")
    article = partner_matching.article(
        review_status="pending",
        publication_status="unpublished",
        published_to_feed_at=None,
        discovered_at=now,
    )
    job = source_readiness.job(
        ArticleEnrichmentJob,
        article_id=article.id,
        status="running",
        created_at=now,
        available_at=now,
        lease_until=now + timedelta(minutes=5),
    )
    with database.begin() as session:
        session.add_all([source, article])
        session.flush()
        session.add_all(
            [
                job,
                ArticleOrigin(
                    article_id=article.id,
                    source_id=source.id,
                    entry_key="ordering",
                    original_url=article.canonical_url,
                ),
            ]
        )

    async def run():
        configured = analytics.settings()
        target = f"current_ordering_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(configured) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                settings = configured.model_copy(update={"clickhouse_database": target})
                async with AnalyticsClient(settings) as client:
                    await analytics.prepare(client, now)
                    worker = CurrentStateReconciler(client, settings)
                    try:
                        old = await worker._snapshot()
                        assert len(old.jobs) == 1
                        old_cards = await asyncio.to_thread(
                            build_cards, settings, "article", [article.id], old.revision
                        )
                        assert old_cards[0]["stage"] == "enrichment"
                        assert old_cards[0]["state"] == "running"

                        # Business writes are committed before their live publication.
                        with database.begin() as session:
                            session.get(Article, article.id).review_status = "rejected"
                            current = session.get(ArticleEnrichmentJob, job.id)
                            current.status, current.outcome = "succeeded", "enriched"
                            current.finished_at, current.lease_token, current.lease_until = (
                                now,
                                None,
                                None,
                            )
                            session.add(
                                ArticleReview(
                                    article_id=article.id,
                                    action="reject",
                                    revision=1,
                                    created_at=now,
                                )
                            )
                        await DirectAnalyticsPublisher(client, settings).publish(
                            [
                                {
                                    "source_table": "article_enrichment_jobs",
                                    "identity": {"id": str(job.id)},
                                    "before_record": old.jobs[0]["record"],
                                }
                            ]
                        )
                        queue_sql = (
                            "SELECT id,revision,status,deleted "
                            "FROM analytics_pipeline_events FINAL "
                            "WHERE domain='queue' AND task='article_enrichment'" + FINAL_GUARDS
                        )
                        card_sql = (
                            "SELECT version,stage,state,deleted "
                            "FROM analytics_card_canonical FINAL "
                            "WHERE policy={policy:String} AND kind='article' "
                            "AND entity_id={id:UUID}" + FINAL_GUARDS
                        )
                        parameters = {
                            "policy": pipeline_policy_version(settings),
                            "id": str(article.id),
                        }
                        live_queue = await client.query(queue_sql)
                        # Business publishers leave canonical cards to their
                        # single owner, the current-state reconciler.
                        assert await client.query(card_sql, parameters) == []
                        await worker._refresh("article", [str(article.id)])
                        live_card = await client.query(card_sql, parameters)
                        assert len(live_queue) == len(live_card) == 1
                        assert int(live_queue[0]["revision"]) > old.revision
                        assert int(live_card[0]["version"]) >> 64 > int(live_queue[0]["revision"])
                        assert live_queue[0]["status"] == "succeeded"
                        assert live_card[0]["stage"] == "rejected"

                        # An old page can arrive after a newer publication's ACK.
                        await client.insert("analytics_card_canonical", old_cards)
                        worker.queue_cycle = old
                        for _ in range(10):
                            await worker._queue_step()
                            if worker.queue_cycle is None:
                                break
                        else:
                            pytest.fail("The delayed old queue cycle did not complete")
                        assert await client.query(queue_sql) == live_queue
                        assert await client.query(card_sql, parameters) == live_card

                        connect = postgres_connections(settings.database_url)
                        events = []
                        connected = asyncio.Event()
                        source_pid = None

                        class TrackedConnection:
                            def __init__(self, connection):
                                self.connection = connection

                            def transaction(self):
                                events.append("begin")
                                return self.connection.transaction()

                            async def execute(self, statement, parameters=None):
                                events.append(
                                    statement
                                    if isinstance(statement, str)
                                    else statement.as_string()
                                )
                                return await self.connection.execute(statement, parameters)

                        @asynccontextmanager
                        async def tracked():
                            nonlocal source_pid
                            try:
                                async with connect() as connection:
                                    source_pid = connection.info.backend_pid
                                    connected.set()
                                    yield TrackedConnection(connection)
                            finally:
                                events.append("closed")

                        async with connect() as holder:
                            await holder.execute("SELECT pg_advisory_lock(%s,%s)", PUBLISH_LOCK)
                            worker.connect = tracked
                            pending = asyncio.create_task(worker._snapshot())
                            try:
                                await asyncio.wait_for(connected.wait(), 3)
                                # Observe the actual PostgreSQL waiter before
                                # releasing the preceding publisher. No RR
                                # snapshot or source read may start in this gap.
                                async with asyncio.timeout(3):
                                    while not await (
                                        await holder.execute(
                                            "SELECT 1 FROM pg_locks WHERE pid=%s "
                                            "AND locktype='advisory' AND NOT granted "
                                            "AND classid::bigint=%s AND objid::bigint=%s "
                                            "AND objsubid=2",
                                            (source_pid, *PUBLISH_LOCK),
                                        )
                                    ).fetchone():
                                        await asyncio.sleep(0.005)
                                assert not pending.done()
                                assert "begin" not in events
                                assert not any(
                                    "txid_current()" in event or " AS source " in event
                                    for event in events
                                )
                                await holder.execute(
                                    "SELECT pg_advisory_unlock(%s,%s)", PUBLISH_LOCK
                                )
                                current = await asyncio.wait_for(pending, 10)
                                assert current.revision > int(live_queue[0]["revision"])
                                assert not current.jobs  # The newer completion is visible.
                            finally:
                                await holder.execute("SELECT pg_advisory_unlock_all()")
                                if not pending.done():
                                    pending.cancel()
                                await asyncio.gather(pending, return_exceptions=True)
                        begin = events.index("begin")
                        fence = next(
                            i for i, event in enumerate(events) if "txid_current()" in event
                        )
                        unlock = next(
                            i for i, event in enumerate(events) if "pg_advisory_unlock" in event
                        )
                        source_read = next(
                            i for i, event in enumerate(events) if " AS source " in event
                        )
                        assert begin < fence < unlock < source_read < events.index("closed")
                        assert await client.query(queue_sql) == live_queue
                        assert await client.query(card_sql, parameters) == live_card

                        # A late business observation cannot recreate a card
                        # after its owner has disappeared and repair ACKed it.
                        with database.begin() as session:
                            session.execute(delete(Article).where(Article.id == article.id))
                        await worker._refresh("article", [str(article.id)])
                        removed = await client.query(card_sql, parameters)
                        assert len(removed) == 1 and removed[0]["deleted"] == 1
                        await DirectAnalyticsPublisher(client, settings).publish(
                            [{"source_table": "articles", "identity": {"id": str(article.id)}}]
                        )
                        assert await client.query(card_sql, parameters) == removed
                    finally:
                        await worker.close()
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())


@pytest.mark.integration
def test_concurrent_publishers_ack_overview_before_reconciliation_fence(database):
    now = datetime.now(UTC)
    article = partner_matching.article(discovered_at=now)
    job = source_readiness.job(
        ArticleEnrichmentJob,
        article_id=article.id,
        status="running",
        created_at=now,
        available_at=now,
        lease_until=now + timedelta(minutes=5),
    )
    with database.begin() as session:
        session.add(article)
        session.flush()
        session.add(job)

    async def run():
        configured = analytics.settings()
        target = f"overview_ordering_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(configured) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                settings = configured.model_copy(update={"clickhouse_database": target})
                async with AnalyticsClient(settings) as client:
                    await analytics.prepare(client, now)
                    entered, released, connected = (
                        asyncio.Event(),
                        asyncio.Event(),
                        asyncio.Event(),
                    )
                    connect = postgres_connections(settings.database_url)
                    source_pid = None

                    class DelayedAck:
                        async def insert(self, table, rows):
                            assert table != "analytics_card_canonical"
                            entered.set()
                            await released.wait()
                            await client.insert(table, rows)

                    @asynccontextmanager
                    async def tracked():
                        nonlocal source_pid
                        async with connect() as connection:
                            source_pid = connection.info.backend_pid
                            connected.set()
                            yield connection

                    worker = CurrentStateReconciler(client, settings, tracked)
                    publisher = DirectAnalyticsPublisher(DelayedAck(), settings)
                    changes = [
                        {
                            "source_table": "article_enrichment_jobs",
                            "identity": {"id": str(job.id)},
                        }
                    ]
                    first = asyncio.create_task(publisher.publish(changes))
                    second = snapshot = None
                    try:
                        await asyncio.wait_for(entered.wait(), 5)
                        # Business work can finish while its earlier running
                        # observation waits for a real ClickHouse INSERT ACK.
                        with database.begin() as session:
                            current = session.get(ArticleEnrichmentJob, job.id)
                            current.status, current.outcome = "succeeded", "enriched"
                            current.finished_at = now
                            current.lease_token = current.lease_until = None
                        second = asyncio.create_task(publisher.publish(changes))
                        async with connect() as observer:
                            async with asyncio.timeout(3):
                                while not await (
                                    await observer.execute(
                                        "SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                                        "AND classid::bigint=%s AND objid::bigint=%s "
                                        "AND objsubid=2 AND NOT granted",
                                        PUBLISH_LOCK,
                                    )
                                ).fetchone():
                                    await asyncio.sleep(0.005)
                            snapshot = asyncio.create_task(worker._snapshot())
                            await asyncio.wait_for(connected.wait(), 3)
                            async with asyncio.timeout(3):
                                while not await (
                                    await observer.execute(
                                        "SELECT 1 FROM pg_locks WHERE pid=%s "
                                        "AND locktype='advisory' AND NOT granted",
                                        (source_pid,),
                                    )
                                ).fetchone():
                                    await asyncio.sleep(0.005)
                            # Waiting remains asynchronous and cannot certify
                            # coverage before both preceding observations ACK.
                            for _ in range(20):
                                await asyncio.sleep(0.001)
                                assert not first.done() and not snapshot.done()
                            released.set()
                            await asyncio.wait_for(asyncio.gather(first, second), 5)
                            cycle = await asyncio.wait_for(snapshot, 5)
                        assert not cycle.jobs
                        rows = await client.query(
                            "SELECT revision,status FROM analytics_pipeline_events FINAL "
                            "WHERE domain='queue' AND task='article_enrichment'" + FINAL_GUARDS
                        )
                        assert len(rows) == 1 and rows[0]["status"] == "succeeded"
                        assert cycle.revision > int(rows[0]["revision"])
                        assert await client.query(
                            "SELECT count() AS count FROM analytics_card_canonical"
                        ) == [{"count": 0}]
                    finally:
                        released.set()
                        for task in (first, second, snapshot):
                            if task is not None and not task.done():
                                task.cancel()
                        await asyncio.gather(
                            *(task for task in (first, second, snapshot) if task is not None),
                            return_exceptions=True,
                        )
                        await worker.close()
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())
