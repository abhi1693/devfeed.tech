"""Current job/card repair against native PostgreSQL and ClickHouse."""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.analytics_direct_cards import publish_card_ids
from devfeed_core.analytics_observation import pipeline_policy_version
from devfeed_core.analytics_overview_direct import DirectOverviewAnalytics
from devfeed_core.analytics_reconcile import (
    FINGERPRINT_TABLE,
    CurrentStateReconciler,
    read_reconciliation_status,
)
from devfeed_core.analytics_status import AnalyticsStatus
from devfeed_core.models import Article, ArticleEnrichmentJob, ArticleOrigin, IngestionJob, Source
from sqlalchemy import delete

from tests.support import analytics


def seed_running_cards(database):
    now = datetime.now(UTC)
    with database() as session:
        source = Source(
            name="Recovery source",
            slug="recovery-source",
            feed_url="https://recovery.example/rss",
            approval_status="approved",
            enabled=True,
            source_type="publisher",
        )
        session.add(source)
        session.flush()
        articles = []
        for suffix in ("completed", "deleted"):
            url = f"https://recovery.example/{suffix}"
            article = Article(
                canonical_url=url,
                url_hash=hashlib.sha256(url.encode()).hexdigest(),
                title=f"Recovery {suffix}",
                discovered_at=now,
            )
            session.add(article)
            session.flush()
            session.add(
                ArticleOrigin(
                    article_id=article.id,
                    source_id=source.id,
                    entry_key=hashlib.sha256(url.encode()).hexdigest(),
                    original_url=url,
                )
            )
            job = ArticleEnrichmentJob(
                article_id=article.id,
                status="running",
                attempts=1,
                lease_token=uuid.uuid4(),
                lease_until=now + timedelta(minutes=5),
            )
            session.add(job)
            session.flush()
            articles.append((article.id, job.id))
        expired = IngestionJob(
            source_id=source.id,
            status="running",
            attempts=1,
            lease_token=uuid.uuid4(),
            lease_until=now - timedelta(minutes=1),
        )
        session.add(expired)
        session.commit()
        return now, articles, expired.id


class UncertainFingerprintAck:
    """The insert persisted, but the caller lost its acknowledgment once."""

    def __init__(self, client):
        self.client = client
        self.settings = client.settings
        self.failed = False

    async def query(self, *args, **kwargs):
        return await self.client.query(*args, **kwargs)

    async def insert(self, table, rows):
        await self.client.insert(table, rows)
        if table == FINGERPRINT_TABLE and not self.failed:
            self.failed = True
            raise AnalyticsUnavailable("Uncertain fingerprint acknowledgment")


@pytest.mark.integration
@pytest.mark.failure_recovery
def test_lost_completions_and_deletions_repair_after_uncertain_ack_and_restart(database):
    now, articles, expired_id = seed_running_cards(database)
    configured = analytics.settings().model_copy(update={"analytics_reconcile_batch_size": 2})

    async def run():
        target = f"current_recovery_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(configured) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                settings = configured.model_copy(update={"clickhouse_database": target})
                async with AnalyticsClient(settings) as client:
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    await analytics.prepare(client, now)
                    await publish_card_ids(client, settings, "article", [a for a, _ in articles], 1)
                    store = DirectOverviewAnalytics(client)
                    await store.jobs(
                        [
                            {
                                "id": job_id,
                                "kind": "article_enrichment",
                                "status": "running",
                                "revision": 1,
                                "attempt": 1,
                                "created_at": now,
                            }
                            for _, job_id in articles
                        ]
                    )
                    await store.jobs(
                        [
                            {
                                "id": uuid.uuid4(),
                                "kind": "ingestion",
                                "status": "succeeded",
                                "revision": 1,
                                "attempt": 1,
                                "created_at": now,
                                "finished_at": now,
                                "duration_ms": 250,
                            }
                        ]
                    )
                    history_sql = (
                        "SELECT count() AS n,sum(cityHash64(tuple(*))) AS checksum "
                        "FROM analytics_pipeline_events WHERE domain='job'"
                    )
                    history_before = await client.query(history_sql)
                    import_before = await client.query("SELECT * FROM analytics_backfill_progress")
                    status_before = await client.query("SELECT * FROM analytics_direct_status")

                    # Simulate accepted business commits whose analytics callbacks were lost.
                    with database() as session:
                        article = session.get(Article, articles[0][0])
                        article.review_status = "rejected"
                        job = session.get(ArticleEnrichmentJob, articles[0][1])
                        job.status, job.outcome, job.finished_at = "succeeded", "enriched", now
                        job.lease_token = job.lease_until = None
                        session.execute(delete(Article).where(Article.id == articles[1][0]))
                        session.commit()

                    faulty = UncertainFingerprintAck(client)
                    first = CurrentStateReconciler(faulty, settings)
                    try:
                        with pytest.raises(AnalyticsUnavailable, match="Uncertain fingerprint"):
                            for _ in range(20):
                                await first.once()
                    finally:
                        await first.close()
                    assert faulty.failed

                    restarted = CurrentStateReconciler(client, settings)
                    try:
                        for _ in range(150):
                            await restarted.once()
                            coverage = await read_reconciliation_status(
                                client, pipeline_policy_version(settings)
                            )
                            if all(
                                row["completed_at"] and not row["pending"]
                                for row in coverage.values()
                            ):
                                break
                        else:
                            pytest.fail("Current-state coverage did not finish after restart")
                    finally:
                        await restarted.close()

                    assert await store.queues() == []
                    cards = await client.query(
                        "SELECT entity_id,stage,state,deleted FROM analytics_card_canonical FINAL "
                        "WHERE policy={policy:String} AND kind='article' ORDER BY entity_id",
                        {"policy": pipeline_policy_version(settings)},
                    )
                    by_id = {str(row["entity_id"]): row for row in cards}
                    assert by_id[str(articles[0][0])]["stage"] == "rejected"
                    assert by_id[str(articles[0][0])]["state"] == "complete"
                    assert by_id[str(articles[1][0])]["deleted"] == 1
                    assert await client.query(history_sql) == history_before
                    assert (
                        await client.query("SELECT * FROM analytics_backfill_progress")
                        == import_before
                    )
                    assert (
                        await client.query("SELECT * FROM analytics_direct_status") == status_before
                    )
                    # Repair changes observations only, never source jobs or editorial decisions.
                    with database() as session:
                        assert session.get(IngestionJob, expired_id).status == "running"
                        assert session.get(Article, articles[0][0]).review_status == "rejected"
                    assert (await AnalyticsStatus(client).read())["ready"] == 1
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())
