"""Native retained-overview recovery preserves source time, epochs, and ACKs."""

import asyncio
import json
import re
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest
from devfeed_core.analytics import AnalyticsClient, AnalyticsPreparing, AnalyticsUnavailable
from devfeed_core.analytics_observation import pipeline_policy_version
from devfeed_core.analytics_overview_direct import DirectOverviewAnalytics
from devfeed_core.analytics_overview_reconcile import (
    OVERVIEW_SOURCES,
    REPORT_SOURCES,
    OverviewReconciler,
    read_overview_snapshot,
    scope_for,
)
from devfeed_core.analytics_reconcile import CurrentStateReconciler
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    TopicProposal,
    UserAccount,
    UserInterest,
    UserRecommendationState,
    UserTopic,
)
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from tests.support import analytics, partner_matching, source_readiness, taxonomy_command_helpers

pytestmark = pytest.mark.integration
NOW = datetime.now(UTC).replace(microsecond=123456)
FINAL = " SETTINGS optimize_move_to_prewhere_if_final=0,enable_optimize_predicate_expression=0"


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self):
        self.now += timedelta(seconds=31)


def article(number, **changes):
    return partner_matching.article(
        **{
            "id": uuid.UUID(int=number),
            "discovered_at": NOW - timedelta(days=3, microseconds=number),
            "published_to_feed_at": NOW - timedelta(hours=number, microseconds=number),
            **changes,
        }
    )


def user(number):
    return UserAccount(
        id=uuid.UUID(int=1000 + number),
        issuer="https://issuer.example.test",
        subject=f"overview-native-{number}",
        organization_id="native",
        created_at=NOW - timedelta(days=30),
    )


def instant(value):
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@asynccontextmanager
async def target():
    configured = analytics.settings()
    database = f"overview_recovery_{uuid.uuid4().hex}_test"
    async with AnalyticsClient(configured) as owner:
        await owner.execute(f"CREATE DATABASE `{database}`")
        try:
            settings = configured.model_copy(
                update={
                    "clickhouse_database": database,
                    "analytics_backfill_batch_size": 1,
                    "analytics_reconcile_sweep_seconds": 30,
                }
            )
            async with AnalyticsClient(settings) as client:
                await analytics.prepare(client, NOW - timedelta(days=10))
                # Keep physical retry/tombstone evidence deterministic. FINAL
                # still exercises native ReplacingMergeTree version selection.
                await client.execute("SYSTEM STOP MERGES analytics_pipeline_events")
                policy = pipeline_policy_version(settings)
                await client.insert(
                    "analytics_reconcile_progress",
                    [
                        {
                            "policy": policy,
                            "scope": scope,
                            "version": 7,
                            "source_at": NOW - timedelta(minutes=1),
                            "completed_at": NOW - timedelta(minutes=1),
                            "pending": 0,
                            "cursor": "{}",
                        }
                        for scope in ("queues", "cards", "active-cards", "sweep")
                    ],
                )
                snapshots = CurrentStateReconciler(client, settings)
                try:
                    yield client, settings, snapshots, Clock()
                finally:
                    await snapshots.close()
        finally:
            await owner.execute(f"DROP DATABASE `{database}` SYNC")


def worker(client, settings, snapshots, clock):
    return OverviewReconciler(client, settings, snapshots._source_snapshot, clock=clock)


async def progress(client, source="articles"):
    rows = await client.query(
        "SELECT * FROM analytics_reconcile_progress FINAL WHERE scope={scope:String}" + FINAL,
        {"scope": scope_for(source)},
    )
    assert len(rows) <= 1
    return rows[0] if rows else None


def assert_cursor_not_advanced(previous, current):
    assert current["pending"] == 1
    assert current["source_at"] == previous["source_at"]
    assert current["completed_at"] == previous["completed_at"]
    before, after = json.loads(previous["cursor"]), json.loads(current["cursor"])
    for key in ("phase", "upper", "after", "target_after", "fence", "started_at"):
        assert after.get(key) == before.get(key), key


async def finish(service, client, source="articles"):
    for _ in range(100):
        await service.once(source)
        row = await progress(client, source)
        if row is not None and not row["pending"]:
            assert json.loads(row["cursor"])["phase"] == "complete"
            return row
    pytest.fail(f"Bounded native overview cycle did not complete: {source}")


async def publications(client):
    return await client.query(
        "SELECT * FROM analytics_pipeline_events FINAL WHERE domain='publication' ORDER BY id"
        + FINAL
    )


async def protected_checkpoints(client):
    return {
        table: await client.query(statement)
        for table, statement in {
            "history": "SELECT * FROM analytics_backfill_progress FINAL ORDER BY import_id,source",
            "status": "SELECT * FROM analytics_direct_status FINAL ORDER BY import_id",
            "pipeline": "SELECT * FROM analytics_reconcile_progress FINAL "
            "WHERE scope NOT LIKE 'overview:%' ORDER BY policy,scope" + FINAL,
        }.items()
    }


async def resume_in_fresh_process(settings, clock, source):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.admin.reporting.test_analytics_overview_reconcile_native",
        "--resume",
        settings.clickhouse_database,
        clock().isoformat(),
        source,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    assert process.returncode == 0, stderr.decode()
    result = json.loads(stdout)
    assert result == {"scope": scope_for(source), "phase": "complete", "pending": 0}


async def resumed_service(database_name, source_time, source):
    # This entrypoint is used only by the restart proof below, in a new Python
    # process with no shared reconciler/client/cursor memory.
    assert re.fullmatch(r"overview_recovery_[a-f0-9]{32}_test", database_name)
    settings = analytics.settings().model_copy(
        update={
            "clickhouse_database": database_name,
            "analytics_backfill_batch_size": 1,
            "analytics_reconcile_sweep_seconds": 30,
        }
    )
    assert (make_url(settings.database_url).database or "").endswith("_test")
    assert urlsplit(settings.redis_url).path == "/15"
    async with AnalyticsClient(settings) as client:
        client.insert_settings = {"async_insert_busy_timeout_ms": 1}
        snapshots = CurrentStateReconciler(client, settings)
        try:
            completed = await finish(
                worker(client, settings, snapshots, Clock(instant(source_time))), client, source
            )
        finally:
            await snapshots.close()
    print(json.dumps({"scope": completed["scope"], "phase": "complete", "pending": 0}))


def test_missing_publications_recover_nonzero_reports_with_original_timestamps(database):
    articles = [article(value) for value in (1, 2, 3)]
    with database.begin() as session:
        session.add_all(articles)

    async def scenario():
        async with target() as (client, settings, snapshots, clock):
            before = await protected_checkpoints(client)
            assert await publications(client) == []
            await finish(worker(client, settings, snapshots, clock), client)
            rows = await publications(client)
            assert len(rows) == 3
            for source, row in zip(articles, rows, strict=True):
                assert row["id"] == str(source.id) and not row["deleted"]
                assert instant(row["occurred_at"]) == source.published_to_feed_at
                assert instant(row["created_at"]) == source.discovered_at
            report = await DirectOverviewAnalytics(client).publication_overview(
                NOW - timedelta(days=1), NOW + timedelta(seconds=1)
            )
            assert sum(int(row["published"]) for row in report if not row["total"]) == 3
            assert [int(row["published"]) for row in report if row["total"]] == [3]
            assert await protected_checkpoints(client) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["before", "after"])
def test_failed_destination_ack_replays_and_restart_resumes_persisted_cursor(
    database, monkeypatch, failure
):
    with database.begin() as session:
        session.add_all([article(1), article(2), article(3)])

    async def scenario():
        async with target() as (client, settings, snapshots, clock):
            before = await protected_checkpoints(client)
            service = worker(client, settings, snapshots, clock)
            await service.once("articles")  # Fixed finite source boundary.
            await service.once("articles")  # First acknowledged source page.
            acknowledged = await progress(client)
            assert json.loads(acknowledged["cursor"])["after"] == [str(uuid.UUID(int=1))]
            original = client.insert
            armed = True

            async def lost_ack(table, rows):
                nonlocal armed
                if table == "analytics_pipeline_events" and armed:
                    armed = False
                    if failure == "after":
                        await original(table, rows)
                    raise AnalyticsUnavailable("Controlled destination acknowledgement loss")
                await original(table, rows)

            monkeypatch.setattr(client, "insert", lost_ack)
            with pytest.raises(AnalyticsUnavailable, match="acknowledgement loss"):
                await service.once("articles")
            assert_cursor_not_advanced(acknowledged, await progress(client))
            assert len(await publications(client)) == (2 if failure == "after" else 1)
            monkeypatch.setattr(client, "insert", original)
            await snapshots.close()
            await resume_in_fresh_process(settings, clock, "articles")
            rows = await publications(client)
            assert len(rows) == 3 and all(not row["deleted"] for row in rows)
            physical = await client.query(
                "SELECT id,count() AS writes FROM analytics_pipeline_events "
                "WHERE domain='publication' GROUP BY id ORDER BY id"
            )
            # The ACKed first page stays untouched. Pending-page recovery may
            # fence and rescan the uncertain page; FINAL still retains one fact.
            assert int(physical[0]["writes"]) == 1
            assert (2 if failure == "after" else 1) <= int(physical[1]["writes"]) <= 3
            assert await protected_checkpoints(client) == before

    asyncio.run(scenario())


def test_deleted_source_publication_is_tombstoned_by_a_later_finite_cycle(database):
    retained, removed = article(1), article(2)
    with database.begin() as session:
        session.add_all([retained, removed])

    async def scenario():
        async with target() as (client, settings, snapshots, clock):
            before = await protected_checkpoints(client)
            service = worker(client, settings, snapshots, clock)
            await finish(service, client)
            with database.begin() as session:
                session.execute(delete(Article).where(Article.id == removed.id))
            clock.advance()
            await finish(service, client)
            rows = {row["id"]: row for row in await publications(client)}
            assert not rows[str(retained.id)]["deleted"]
            assert rows[str(removed.id)]["deleted"] == 1
            epoch = str(json.loads((await progress(client))["cursor"])["fence"])
            presence = await client.query(
                "SELECT fingerprint,owners FROM analytics_reconcile_fingerprints FINAL "
                "WHERE kind='overview-target:articles' AND deleted=0" + FINAL
            )
            seen = {json.loads(row["owners"][0])[2]: row["fingerprint"] for row in presence}
            assert seen[str(retained.id)] == epoch
            assert seen[str(removed.id)] != epoch
            assert await protected_checkpoints(client) == before

    asyncio.run(scenario())


def test_late_live_publication_survives_an_already_selected_absence_tombstone(
    database, monkeypatch
):
    identity = uuid.UUID(int=9)

    async def scenario():
        async with target() as (client, settings, snapshots, clock):
            before = await protected_checkpoints(client)
            await DirectOverviewAnalytics(client).publications(
                [{"article_id": str(identity), "revision": 1, "published_at": NOW}]
            )
            service = worker(client, settings, snapshots, clock)
            await service.once("articles")
            start = json.loads((await progress(client))["cursor"])["fence"]
            await service.once("articles")
            assert json.loads((await progress(client))["cursor"])["phase"] == "target"
            original = client.query
            late_revision = None
            late_time = NOW - timedelta(minutes=5)

            async def late_writer(statement, parameters=None):
                nonlocal late_revision
                rows = await original(statement, parameters)
                if statement.startswith("SELECT domain,id FROM analytics_pipeline_events FINAL"):
                    assert rows and late_revision is None
                    with database.begin() as session:
                        session.add(article(9, published_to_feed_at=late_time))
                    async with snapshots._source_snapshot() as (_connection, revision, _observed):
                        late_revision = revision
                    assert late_revision > start
                    await DirectOverviewAnalytics(client).publications(
                        [
                            {
                                "article_id": str(identity),
                                "revision": late_revision,
                                "published_at": late_time,
                            }
                        ]
                    )
                    monkeypatch.setattr(client, "query", original)
                return rows

            monkeypatch.setattr(client, "query", late_writer)
            await finish(service, client)
            (row,) = await publications(client)
            assert not row["deleted"] and int(row["revision"]) == late_revision
            assert instant(row["occurred_at"]) == late_time
            tombstones = await client.query(
                "SELECT revision FROM analytics_pipeline_events WHERE domain='publication' "
                "AND id={id:UUID} AND deleted=1",
                {"id": str(identity)},
            )
            assert tombstones and all(int(row["revision"]) == start for row in tombstones)
            assert await protected_checkpoints(client) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("pending", [False, True])
def test_recovered_completed_job_attempts_never_create_queue_observations(
    database, monkeypatch, pending
):
    source = article(1)
    finished = NOW - timedelta(minutes=7, microseconds=7)
    job = source_readiness.job(
        ArticleAnalysisJob,
        status="succeeded",
        article_id=source.id,
        attempts=2,
        finished_at=finished,
        duration_ms=1234,
        outcome="analyzed",
    )
    with database.begin() as session:
        session.add(source)
        session.flush()
        session.add(job)
    assert "article_analysis_jobs" in OVERVIEW_SOURCES

    async def scenario():
        async with target() as (client, settings, snapshots, clock):
            before = await protected_checkpoints(client)
            service = worker(client, settings, snapshots, clock)
            if pending:
                await service.once("article_analysis_jobs")
                original = client.insert

                async def interrupted_attempt(table, rows):
                    if table == "analytics_pipeline_events":
                        raise AnalyticsUnavailable("Controlled pending-attempt interruption")
                    await original(table, rows)

                monkeypatch.setattr(client, "insert", interrupted_attempt)
                with pytest.raises(AnalyticsUnavailable, match="pending-attempt"):
                    await service.once("article_analysis_jobs")
                state = json.loads((await progress(client, "article_analysis_jobs"))["cursor"])
                assert len(state["pending_page"]["immutable"]) == 1
                monkeypatch.setattr(client, "insert", original)
            else:
                await finish(service, client, "article_analysis_jobs")
            with database.begin() as session:
                session.execute(delete(ArticleAnalysisJob).where(ArticleAnalysisJob.id == job.id))
            if pending:
                await resume_in_fresh_process(settings, clock, "article_analysis_jobs")
            else:
                clock.advance()
                await finish(service, client, "article_analysis_jobs")
            rows = await client.query(
                "SELECT * FROM analytics_pipeline_events FINAL ORDER BY domain,id" + FINAL
            )
            assert len(rows) == 1 and rows[0]["domain"] == "job"
            assert rows[0]["task"] == "analysis"
            assert rows[0]["attempt"] == 2 and rows[0]["status"] == "succeeded"
            assert int(rows[0]["duration_ms"]) == 1234 and rows[0]["outcome"] == "analyzed"
            assert instant(rows[0]["occurred_at"]) == finished
            assert await protected_checkpoints(client) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("removed_source", ["user_topics", "user_interests"])
def test_composite_memberships_page_and_cleanup_only_their_owned_kind(database, removed_source):
    users = [user(1), user(2)]
    topics = [
        partner_matching.topic(f"Native topic {number}", id=uuid.UUID(int=number))
        for number in (201, 202, 203)
    ]
    pairs = [(users[0].id, topics[0].id), (users[0].id, topics[1].id), (users[1].id, topics[0].id)]
    followed_at = {
        pair: NOW - timedelta(days=10, minutes=index) for index, pair in enumerate(pairs)
    }
    with database.begin() as session:
        session.add_all([*users, *topics])
        session.flush()
        for current in users:
            # User insertion creates its durable recommendation state.
            state = session.get(UserRecommendationState, current.id)
            assert state is not None
            state.generation = uuid.uuid4()
            state.invalidated = False
            state.computed_at = NOW - timedelta(hours=1)
            state.expires_at = NOW + timedelta(hours=1)
            state.interest_count = 2
        session.add_all(
            [
                UserTopic(user_id=uid, topic_id=tid, created_at=followed_at[uid, tid])
                for uid, tid in pairs
            ]
        )
        session.add_all(
            [
                UserInterest(
                    user_id=uid, topic_id=tid, seed_topic_id=tid, weight=1, reason="related_topic"
                )
                for uid, tid in pairs
            ]
        )

    async def scenario():
        async with target() as (client, settings, snapshots, clock):
            before = await protected_checkpoints(client)
            # Legacy observations have no source or presence fingerprint yet.
            await DirectOverviewAnalytics(client).memberships(
                [
                    {
                        "user_id": str(users[0].id),
                        "topic_id": str(topics[2].id),
                        "kind": kind,
                        "revision": 1,
                        "occurred_at": NOW - timedelta(days=20),
                    }
                    for kind in ("follow", "inferred")
                ]
            )
            service = worker(client, settings, snapshots, clock)
            for source in ("user_topics", "user_interests"):
                await service.once(source)
                await service.once(source)
                assert json.loads((await progress(client, source))["cursor"])["after"] == [
                    str(value) for value in pairs[0]
                ]
                await finish(service, client, source)
            rows = await client.query(
                "SELECT * FROM analytics_user_events FINAL WHERE domain='membership'" + FINAL
            )
            assert len(rows) == 8
            live = [row for row in rows if not row["deleted"]]
            assert len(live) == 6
            identities = {
                (row["task"], row["user_id"], row["target_id"]): row["id"] for row in live
            }
            for row in live:
                pair = uuid.UUID(row["user_id"]), uuid.UUID(row["target_id"])
                assert pair in pairs
                assert (
                    instant(row["occurred_at"]) == followed_at[pair]
                    if row["task"] == "follow"
                    else instant(row["occurred_at"]) == NOW - timedelta(hours=1)
                )
            removed_model = UserTopic if removed_source == "user_topics" else UserInterest
            with database.begin() as session:
                session.execute(
                    delete(removed_model).where(
                        removed_model.user_id == pairs[1][0], removed_model.topic_id == pairs[1][1]
                    )
                )
            clock.advance()
            await finish(service, client, removed_source)
            rows = await client.query(
                "SELECT * FROM analytics_user_events FINAL WHERE domain='membership'" + FINAL
            )
            assert sum(not row["deleted"] for row in rows) == 5
            removed_kind = "follow" if removed_source == "user_topics" else "inferred"
            by_id = {row["id"]: row for row in rows}
            for kind in ("follow", "inferred"):
                identity = identities[kind, str(pairs[1][0]), str(pairs[1][1])]
                assert by_id[identity]["deleted"] == int(kind == removed_kind)
            assert await protected_checkpoints(client) == before

    asyncio.run(scenario())


def test_empty_cycles_cover_every_retained_source_and_gate_initial_reports(database):
    async def scenario():
        async with target() as (client, settings, snapshots, clock):
            before = await protected_checkpoints(client)
            reads = []

            class TrackedConnection:
                def __init__(self, connection):
                    self.connection = connection

                async def execute(self, statement, parameters=None):
                    reads.append(statement if isinstance(statement, str) else statement.as_string())
                    return await self.connection.execute(statement, parameters)

            @asynccontextmanager
            async def tracked_snapshot():
                async with snapshots._source_snapshot() as (connection, revision, observed):
                    yield TrackedConnection(connection), revision, observed

            service = OverviewReconciler(client, settings, tracked_snapshot, clock=clock)
            with pytest.raises(AnalyticsPreparing):
                await read_overview_snapshot(
                    client,
                    service.policy,
                    REPORT_SOURCES["publishing"],
                    datetime.now(UTC),
                    generation="native",
                )
            for source in OVERVIEW_SOURCES:
                if source == OVERVIEW_SOURCES[-1]:
                    with pytest.raises(AnalyticsPreparing):
                        await read_overview_snapshot(
                            client,
                            service.policy,
                            REPORT_SOURCES["processing"],
                            datetime.now(UTC),
                            generation="native",
                        )
                await finish(service, client, source)
                if source == "articles":
                    publishing = await read_overview_snapshot(
                        client,
                        service.policy,
                        REPORT_SOURCES["publishing"],
                        datetime.now(UTC),
                        generation="native",
                    )
                    assert publishing.status == "fresh"
            all_sources = await read_overview_snapshot(
                client, service.policy, OVERVIEW_SOURCES, datetime.now(UTC), generation="native"
            )
            assert all_sources.status == "fresh"
            tables = {
                match.group(1)
                for statement in reads
                if (match := re.search(r'\bFROM\s+"?([a-z_]+)"?', statement))
            }
            assert tables == set(OVERVIEW_SOURCES)  # No retired PostgreSQL tables or fallback.
            assert await publications(client) == []
            assert await protected_checkpoints(client) == before

    asyncio.run(scenario())


def test_fanout_above_one_thousand_observations_retries_after_partial_ack(database, monkeypatch):
    with database.begin() as session:
        session.add_all([user(number) for number in range(1, 502)])

    async def scenario():
        async with target() as (client, settings, snapshots, clock):
            settings = settings.model_copy(update={"analytics_backfill_batch_size": 1000})
            before = await protected_checkpoints(client)
            service = worker(client, settings, snapshots, clock)
            await service.once("user_accounts")
            previous = await progress(client, "user_accounts")
            original = client.insert
            chunks = []

            async def interrupted_second_chunk(table, rows):
                if table == "analytics_user_events":
                    chunks.append(len(rows))
                    if len(chunks) == 2:
                        raise AnalyticsUnavailable("Controlled second-chunk acknowledgement loss")
                await original(table, rows)

            monkeypatch.setattr(client, "insert", interrupted_second_chunk)
            with pytest.raises(AnalyticsUnavailable, match="second-chunk"):
                await service.once("user_accounts")
            assert chunks == [1000, 2]
            assert_cursor_not_advanced(previous, await progress(client, "user_accounts"))
            fingerprints = await client.query(
                "SELECT count() AS count FROM analytics_reconcile_fingerprints "
                "WHERE kind='overview-source:user_accounts'"
            )
            assert int(fingerprints[0]["count"]) == 0
            monkeypatch.setattr(client, "insert", original)
            await finish(service, client, "user_accounts")
            counts = await client.query(
                "SELECT domain,count() AS count FROM analytics_user_events FINAL "
                "WHERE deleted=0 GROUP BY domain" + FINAL
            )
            assert {row["domain"]: int(row["count"]) for row in counts} == {
                "account": 501,
                "feed": 501,
            }
            assert await publications(client) == []
            assert await protected_checkpoints(client) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["delete", "incomplete"])
def test_completed_topic_decision_history_survives_source_retirement(database, change):
    reviewed = NOW - timedelta(hours=2)
    proposal = taxonomy_command_helpers.proposal(
        status="rejected", reviewed_at=reviewed, reviewed_by={"id": "native-reviewer"}
    )
    with database.begin() as session:
        session.add(proposal)

    async def scenario():
        async with target() as (client, settings, snapshots, clock):
            before = await protected_checkpoints(client)
            service = worker(client, settings, snapshots, clock)
            await finish(service, client, "topic_proposals")
            statement = (
                "SELECT * FROM analytics_pipeline_events FINAL "
                "WHERE domain='job' AND task='topic-decision'" + FINAL
            )
            completed = await client.query(statement)
            assert len(completed) == 1 and not completed[0]["deleted"]
            assert instant(completed[0]["occurred_at"]) == reviewed
            with database.begin() as session:
                if change == "delete":
                    session.execute(delete(TopicProposal).where(TopicProposal.id == proposal.id))
                else:
                    current = session.get(TopicProposal, proposal.id)
                    current.status, current.reviewed_at, current.reviewed_by = "pending", None, None
            # The shared live writer and repair both preserve a completed fact.
            await DirectOverviewAnalytics(client).publish(
                [
                    {
                        "source_table": "topic_proposals",
                        "identity": {"id": str(proposal.id)},
                        "record": None
                        if change == "delete"
                        else {"id": str(proposal.id), "status": "pending"},
                    }
                ],
                revision=int(completed[0]["revision"]) + 1,
            )
            clock.advance()
            await finish(service, client, "topic_proposals")
            assert await client.query(statement) == completed
            assert await protected_checkpoints(client) == before

    asyncio.run(scenario())


async def pending_insert(client, database_name):
    return await client.query(
        "SELECT total_bytes FROM system.asynchronous_inserts "
        "WHERE database={database:String} AND table='analytics_pipeline_events'",
        {"database": database_name},
    )


def test_native_insert_arriving_after_cancel_restart_cannot_resurrect_deleted_source(
    database, monkeypatch
):
    source = article(1)
    with database.begin() as session:
        session.add(source)

    async def scenario():
        async with target() as (client, settings, snapshots, clock):
            before = await protected_checkpoints(client)
            service = worker(client, settings, snapshots, clock)
            await service.once("articles")
            original = client.insert

            async def buffered_destination(table, rows):
                if table != "analytics_pipeline_events":
                    return await original(table, rows)
                async with AnalyticsClient(settings) as delayed:
                    delayed.insert_settings = {
                        "async_insert_busy_timeout_ms": 60000,
                        "async_insert_use_adaptive_busy_timeout": 0,
                    }
                    return await delayed.insert(table, rows)

            monkeypatch.setattr(client, "insert", buffered_destination)
            work = asyncio.create_task(service.once("articles"))
            async with AnalyticsClient(settings) as observer:
                try:
                    async with asyncio.timeout(5):
                        while not await pending_insert(observer, settings.clickhouse_database):
                            await asyncio.sleep(0.01)
                    assert await publications(observer) == []
                    pending = json.loads((await progress(observer))["cursor"])["pending_page"]
                    assert pending["keys"] == [[str(source.id)]]
                    old_revision = pending["revision"]
                    work.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await work
                    await client.close()
                    await snapshots.close()
                    assert await pending_insert(observer, settings.clickhouse_database)
                    with database.begin() as session:
                        session.execute(delete(Article).where(Article.id == source.id))
                    await resume_in_fresh_process(settings, clock, "articles")
                    assert await pending_insert(observer, settings.clickhouse_database)
                    coverage = await read_overview_snapshot(
                        observer,
                        pipeline_policy_version(settings),
                        REPORT_SOURCES["publishing"],
                        datetime.now(UTC),
                        generation="native",
                    )
                    assert coverage.status == "fresh"
                    # The old request is genuinely held in ClickHouse, not a mocked
                    # client; it arrives only after the replacement process exits.
                    await observer.execute("SYSTEM FLUSH ASYNC INSERT QUEUE")
                    rows = await publications(observer)
                    assert len(rows) == 1 and rows[0]["deleted"] == 1
                    assert int(rows[0]["revision"]) > old_revision
                    physical = await observer.query(
                        "SELECT revision,deleted FROM analytics_pipeline_events "
                        "WHERE domain='publication' AND id={id:UUID}",
                        {"id": str(source.id)},
                    )
                    assert any(
                        not row["deleted"] and int(row["revision"]) == old_revision
                        for row in physical
                    )
                    assert await protected_checkpoints(observer) == before
                finally:
                    if not work.done():
                        work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
                    await observer.execute("SYSTEM FLUSH ASYNC INSERT QUEUE")

    asyncio.run(scenario())


if __name__ == "__main__":
    assert len(sys.argv) == 5 and sys.argv[1] == "--resume"
    asyncio.run(resumed_service(*sys.argv[2:]))
