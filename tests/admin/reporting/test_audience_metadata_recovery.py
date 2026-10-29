"""Native audience metadata upgrade, bounded recovery and rolling-writer fencing."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core import analytics_audience_refresh as refresh
from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.analytics_migrations import ClickHouseMigrationRunner, load_migrations
from devfeed_core.analytics_overview_direct import _base, _identity
from devfeed_core.analytics_reconcile import CurrentStateReconciler
from devfeed_core.models import Topic, UserAccount, UserInterest, UserRecommendationState, UserTopic

from tests.support import analytics

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def identity(value):
    return str(uuid.UUID(int=value))


def seed(database):
    with database.begin() as session:
        session.add_all(
            UserAccount(
                id=uuid.UUID(int=i),
                issuer="fixture",
                subject=str(i),
                organization_id="fixture",
                created_at=NOW - timedelta(days=100),
            )
            for i in range(1, 102)
        )
        session.add_all(
            Topic(
                id=uuid.UUID(int=i),
                name=f"Metadata {i}",
                slug=f"metadata-{i}",
                kind="concept",
                status="active",
            )
            for i in range(1, 202)
        )
        session.flush()
        session.add_all(
            UserTopic(
                user_id=uuid.UUID(int=1),
                topic_id=uuid.UUID(int=i),
                created_at=NOW - timedelta(days=5),
            )
            for i in range(1, 202)
        )
        session.add_all(
            UserInterest(
                user_id=uuid.UUID(int=2),
                topic_id=uuid.UUID(int=i),
                seed_topic_id=uuid.UUID(int=i),
                weight=100,
                reason="related_topic",
            )
            for i in range(1, 202)
        )
        state = session.get(UserRecommendationState, uuid.UUID(int=2))
        assert state is not None  # Account/profile writes already schedule durable feed work.
        state.computed_at = NOW


class UncertainAck:
    def __init__(self, client):
        self.client, self.settings, self.failed = client, client.settings, False

    async def query(self, *args, **kwargs):
        return await self.client.query(*args, **kwargs)

    async def insert(self, table, rows):
        await self.client.insert(table, rows)
        if table == "analytics_user_events" and rows[0]["task"] == "follow" and not self.failed:
            self.failed = True
            raise AnalyticsUnavailable("Uncertain membership acknowledgement")


async def first_pass(client, settings, database):
    faulty = UncertainAck(client)
    first = CurrentStateReconciler(faulty, settings)
    try:
        assert await first._lead()
        assert await first._audience_metadata.once()  # Fix the account ceiling at101.
        assert await first._audience_metadata.once()  # First100 accounts acknowledged.
        with database.begin() as session:
            session.add(
                UserAccount(
                    id=uuid.UUID(int=0),
                    issuer="fixture",
                    subject="late",
                    organization_id="fixture",
                    created_at=NOW,
                )
            )
        async with first._source_snapshot() as snapshot:
            assert snapshot is not None
            late_revision = snapshot[1]
        # An old rolling worker can deliver behind the source cursor.
        await client.insert("analytics_user_events", [_base("feed", identity(0), late_revision)])
        with pytest.raises(AnalyticsUnavailable, match="Uncertain membership"):
            for _ in range(10):
                await first._audience_metadata.once()
    finally:
        await first.close()
    assert faulty.failed
    return late_revision


async def resumed_pass(client, settings, late_revision):
    resumed = CurrentStateReconciler(client, settings)
    try:
        assert await resumed._lead()
        for _ in range(20):
            await resumed._audience_metadata.once()
            if resumed._audience_metadata.complete:
                break
        else:
            pytest.fail("First finite native pass did not finish")
        zero = await client.query(
            "SELECT revision,deleted FROM analytics_user_events FINAL "
            "WHERE domain='feed' AND id={id:UUID}",
            {"id": identity(0)},
        )
        assert zero == [{"revision": late_revision, "deleted": 0}]
        assert not await refresh.audience_metadata_ready(client)
        for _ in range(30):
            if not await resumed._audience_metadata.once():
                break
        assert await refresh.audience_metadata_ready(client)
    finally:
        await resumed.close()


@pytest.mark.integration
def test_native_upgrade_paged_metadata_restart_orphans_and_late_old_writer(database):
    seed(database)

    async def run():
        configured = analytics.settings()
        target = f"audience_refresh_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(configured) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                settings = configured.model_copy(update={"clickhouse_database": target})
                async with AnalyticsClient(settings) as client:
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    migrations = load_migrations()
                    old = ClickHouseMigrationRunner(client, migrations=migrations[:3])
                    assert await old.upgrade() == (1, 2, 3)
                    orphan = _base("feed", identity(900), 1)
                    old_follow = _base(
                        "membership",
                        _identity("membership", "follow", identity(901), identity(1)),
                        1,
                    )
                    old_follow.update(task="follow", target_id=identity(1))
                    await client.insert("analytics_user_events", [orphan, old_follow])
                    assert await ClickHouseMigrationRunner(client).upgrade() == (4,)
                    assert await ClickHouseMigrationRunner(client).upgrade() == ()
                    before = await client.query("SELECT * FROM analytics_backfill_progress")
                    assert not await refresh.audience_metadata_ready(client)
                    late_revision = await first_pass(client, settings, database)
                    await resumed_pass(client, settings, late_revision)
                    rows = await client.query(
                        "SELECT domain,task,count() AS n FROM analytics_user_events FINAL "
                        "WHERE deleted=0 GROUP BY domain,task"
                    )
                    assert {(row["domain"], row["task"]): row["n"] for row in rows} == {
                        ("feed", ""): 102,
                        ("membership", "follow"): 201,
                        ("membership", "inferred"): 201,
                    }
                    dates = await client.query(
                        "SELECT task,min(occurred_at) AS first,max(occurred_at) AS last "
                        "FROM analytics_user_events FINAL WHERE deleted=0 "
                        "AND domain='membership' GROUP BY task"
                    )
                    follow_time = next(row for row in dates if row["task"] == "follow")["first"]
                    inferred_time = next(row for row in dates if row["task"] == "inferred")["last"]
                    assert datetime.fromisoformat(follow_time.replace("Z", "+00:00")) == (
                        NOW - timedelta(days=5)
                    )
                    assert datetime.fromisoformat(inferred_time.replace("Z", "+00:00")) == NOW
                    tombstones = await client.query(
                        "SELECT count() AS n FROM analytics_user_events FINAL WHERE deleted=1"
                    )
                    assert tombstones == [{"n": 2}]
                    assert await client.query("SELECT * FROM analytics_backfill_progress") == before
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())
