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

from tests.support import analytics, partner_matching, source_readiness


@pytest.mark.integration
def test_late_reconciliation_preserves_newer_live_queue_and_card_and_skips_a_busy_publisher(
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
                        assert old is not None and len(old.jobs) == 1
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
                        live_card = await client.query(card_sql, parameters)
                        assert len(live_queue) == len(live_card) == 1
                        assert int(live_queue[0]["revision"]) > old.revision
                        assert int(live_card[0]["version"]) >> 64 == int(live_queue[0]["revision"])
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
                            async with connect() as connection:
                                yield TrackedConnection(connection)

                        async with connect() as holder:
                            await holder.execute("SELECT pg_advisory_lock(%s,%s)", PUBLISH_LOCK)
                            try:
                                worker.connect = tracked
                                assert await asyncio.wait_for(worker._snapshot(), timeout=1) is None
                            finally:
                                await holder.execute(
                                    "SELECT pg_advisory_unlock(%s,%s)", PUBLISH_LOCK
                                )
                        assert "begin" not in events
                        assert not any(
                            "txid_current()" in event or " AS source " in event for event in events
                        )
                    finally:
                        await worker.close()
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())
