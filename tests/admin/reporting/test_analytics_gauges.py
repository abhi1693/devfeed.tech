"""Current analytics stay shard-bounded, replay-safe, and independently published."""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_gauges import Gauges, shard
from devfeed_core.config import Settings

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
USER = uuid.UUID("10000000-0000-0000-0000-000000000001")
TOPIC = uuid.UUID("20000000-0000-0000-0000-000000000002")
SOURCE = uuid.UUID("30000000-0000-0000-0000-000000000003")


class Connection:
    def __init__(self, *, rows=(), row=None):
        self.rows, self.row, self.calls = rows, row, []

    async def execute(self, statement, parameters=()):
        self.calls.append((str(statement), parameters))
        return SimpleNamespace(
            fetchall=AsyncMock(return_value=self.rows), fetchone=AsyncMock(return_value=self.row)
        )

    @asynccontextmanager
    async def transaction(self):
        yield


def event(source, record, *, version=1, operation="I"):
    return dict(id=version, source_table=source, record=record, operation=operation, created_at=NOW)


def client():
    return SimpleNamespace(execute=AsyncMock(), insert=AsyncMock(), query=AsyncMock())


@pytest.mark.parametrize("exists", [True, False])
def test_changed_users_are_hydrated_once_without_identity_or_recommendation_payloads(exists):
    row = dict(
        id=USER,
        interest_count=2,
        invalidated=False,
        generation=uuid.uuid4(),
        expires_at=NOW + timedelta(hours=1),
        next_refresh_at=NOW,
        has_recommendations=True,
    )
    connection = Connection(rows=[row] if exists else [])
    driver = client()

    async def run():
        await Gauges(None, driver).ingest(
            connection,
            [
                event("user_accounts", {"id": str(USER), "profile": {"email": "private"}}),
                event("user_recommendation_states", {"user_id": str(USER)}, version=9),
            ],
            generation=7,
        )

    asyncio.run(run())
    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    assert "WHERE a.id=ANY" in sql and "LIMIT 1" in sql
    assert params == ([USER],)
    rows = driver.insert.await_args.args[1]
    assert len(rows) == 2 and {row["kind"] for row in rows} == {"account", "feed"}
    assert all(row["event_id"] == 9 and row["delivery_generation"] == 7 for row in rows)
    assert all(row["captured_at"] == NOW and row["deleted"] == int(not exists) for row in rows)
    assert "private" not in str(rows) and "profile" not in str(rows)


def test_gauge_capture_preserves_relevant_dimensions_and_waits_for_partial_failures():
    done = []
    driver = client()

    async def insert(table, _rows):
        if "dimension" in table:
            raise RuntimeError("sink offline")
        await asyncio.sleep(0)
        done.append(table)

    driver.insert.side_effect = insert

    async def run():
        with pytest.raises(RuntimeError, match="sink offline"):
            await Gauges(None, driver).ingest(
                Connection(),
                [
                    event(
                        "sources",
                        {"id": str(SOURCE), "name": "Publisher", "consecutive_failures": -1},
                    ),
                    event("user_topics", {"user_id": str(USER), "topic_id": str(TOPIC)}),
                ],
                generation=3,
            )

    asyncio.run(run())
    assert done == ["analytics_membership_changes"]
    records = driver.insert.await_args_list[0].args[1]
    assert records[0]["consecutive_failures"] == 0


@pytest.mark.parametrize("reason,deleted", [("related_topic", 0), ("explicit_topic", 1)])
def test_topic_interest_corrections_remove_the_old_inferred_membership(reason, deleted):
    driver = client()
    asyncio.run(
        Gauges(None, driver).ingest(
            Connection(),
            [
                event(
                    "user_interests",
                    dict(
                        user_id=str(USER),
                        topic_id=str(TOPIC),
                        reason=reason,
                    ),
                )
            ],
            generation=4,
        )
    )
    row = driver.insert.await_args.args[1][0]
    assert row["kind"] == "inferred" and row["deleted"] == deleted
    assert row["shard"] == shard(TOPIC) and row["captured_at"] == NOW


def test_dirty_keys_coalesce_and_truncation_has_a_finite_shard_space():
    connection = Connection()
    asyncio.run(
        Gauges(None, client()).mark_dirty(
            connection,
            [
                event("user_accounts", {"id": str(USER)}),
                event("user_recommendation_states", {"user_id": str(USER)}),
                event("article_analysis_jobs", {}),
                event("ingestion_jobs", {}),
                event("topics", {}, operation="T"),
                event("article_origins", {}),
            ],
        )
    )
    assert len(connection.calls) == 1
    domains, shards = connection.calls[0][1]
    targets = list(zip(domains, shards, strict=True))
    assert len(targets) == 258
    assert targets.count(("feed", shard(USER))) == 1
    assert targets.count(("queues", 0)) == 1
    assert {number for domain, number in targets if domain == "topics"} == set(range(256))
    assert all("LEAST" in statement and "nextval" in statement for statement, _ in connection.calls)


@pytest.mark.parametrize("domain", ["queues", "feed", "sources", "topics"])
def test_complete_gauge_generation_is_published_before_revision_fenced_ack(domain):
    connection = Connection(rows=[dict(domain=domain, shard=0, revision=17)])
    driver = client()
    gauge = Gauges(None, driver)
    gauge._queues = AsyncMock(
        return_value=(
            [{"kind": "analysis", "queued": 3, "running": 1, "oldest_queued_at": None}],
            NOW + timedelta(days=1),
        )
    )
    gauge._feed_many = AsyncMock(return_value=([{"shard": 0, "ready": 2}], {0: None}))
    gauge._dimensions_many = AsyncMock(return_value=([], {0: None}))
    assert asyncio.run(gauge.refresh_one(connection, 9))
    assert driver.insert.await_args_list[-1].args[0] == "analytics_gauge_manifest"
    manifest = driver.insert.await_args_list[-1].args[1][0]
    assert manifest["generation"] == 9 and manifest["domain"] == domain
    statement, params = connection.calls[-2]
    assert statement.startswith("UPDATE") and "r.revision=done.revision" in statement
    assert params[:2] == ([0], [17]) and params[3] == domain
    assert "bootstrap_generation<=%s" in connection.calls[-1][0]
    assert connection.calls[-1][1] == (domain, [0], 9)


def test_failed_gauge_write_cannot_publish_a_manifest_or_acknowledge_dirty_work():
    connection = Connection(rows=[dict(domain="feed", shard=0, revision=3)])
    driver = client()
    driver.insert.side_effect = RuntimeError("sink offline")
    gauge = Gauges(None, driver)
    gauge._feed_many = AsyncMock(return_value=([{"shard": 0, "ready": 2}], {0: None}))
    with pytest.raises(RuntimeError, match="sink offline"):
        asyncio.run(gauge.refresh_one(connection, 4))
    assert len(connection.calls) == 1
    assert driver.insert.await_count == 1
    assert driver.insert.await_args.args[0] == "analytics_feed_gauges"


def test_idle_refresh_and_readiness_do_not_scan_source_populations():
    connection = Connection()
    assert not asyncio.run(Gauges(None, client()).refresh_one(connection, 1))
    connection.row = {"complete": True, "started": True}
    assert asyncio.run(Gauges(None, client()).bootstrap_complete(connection))
    assert len(connection.calls) == 3
    assert all("user_accounts" not in statement for statement, _ in connection.calls)
    assert "WHERE bootstrap_required" in connection.calls[-1][0]


def test_initial_gauge_coverage_is_fenced_from_later_continuous_changes():
    connection = Connection(row={"started": False, "complete": False})
    assert not asyncio.run(Gauges(None, client()).bootstrap_complete(connection))
    assert "FOR UPDATE" in connection.calls[0][0]
    assert "bootstrap_generation=(SELECT lease_generation+1" in connection.calls[1][0]
    assert "SET started=true" in connection.calls[2][0]
    assert "WHERE bootstrap_required" in connection.calls[3][0]


def _settings():
    endpoint = os.environ.get("DEVFEED_TEST_CLICKHOUSE_URL")
    if not endpoint:
        pytest.skip("Set DEVFEED_TEST_CLICKHOUSE_URL for native gauge integration")
    database = os.environ.get("DEVFEED_TEST_CLICKHOUSE_DATABASE", "devfeed_test")
    assert database.endswith("_test")
    return Settings(
        _env_file=None,
        database_url="postgresql://unused",
        redis_url="redis://unused",
        clickhouse_url=endpoint,
        clickhouse_database=database,
        clickhouse_username=os.environ.get("DEVFEED_TEST_CLICKHOUSE_USERNAME", "default"),
        clickhouse_password=os.environ.get("DEVFEED_TEST_CLICKHOUSE_PASSWORD", ""),
    )


@pytest.mark.integration
def test_native_states_deduplicate_replay_and_honor_corrections_and_resets():
    settings = _settings()

    async def run():
        database = f"devfeed_gauges_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(settings) as admin:
            await admin.execute(f"CREATE DATABASE `{database}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": database})
                ) as driver:
                    gauge = Gauges(settings, driver)
                    await gauge.initialize()
                    row = dict(
                        id=USER,
                        interest_count=2,
                        invalidated=False,
                        generation=uuid.uuid4(),
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                        next_refresh_at=datetime.now(UTC) + timedelta(hours=1),
                        has_recommendations=True,
                    )
                    events = [event("user_accounts", {"id": str(USER)})]
                    for _ in range(2):
                        await gauge.ingest(Connection(rows=[row]), events, generation=1)
                    feed, _ = await gauge._feed(shard(USER))
                    assert feed[0]["ready"] == 1 and feed[0]["not_needed"] == 0
                    await gauge.ingest(
                        Connection(),
                        [
                            event(
                                "topics", {"id": str(TOPIC), "name": "Python", "status": "active"}
                            ),
                            event(
                                "sources",
                                {"id": str(SOURCE), "name": "Publisher", "consecutive_failures": 2},
                            ),
                            event("user_topics", {"user_id": str(USER), "topic_id": str(TOPIC)}),
                        ],
                        generation=2,
                    )
                    topics, _ = await gauge._dimensions("topics", shard(TOPIC))
                    assert topics[0]["followers"] == 1 and topics[0]["name"] == "Python"
                    sources, _ = await gauge._dimensions("sources", shard(SOURCE))
                    assert sources[0]["consecutive_failures"] == 2
                    deletion = event(
                        "user_topics",
                        {"user_id": str(USER), "topic_id": str(TOPIC)},
                        version=2,
                        operation="D",
                    )
                    await gauge.ingest(Connection(), [deletion], generation=3)
                    # An older delivery with a larger outbox ID cannot resurrect an edge.
                    await gauge.ingest(
                        Connection(),
                        [event("user_topics", deletion["record"], version=99)],
                        generation=2,
                    )
                    topics, _ = await gauge._dimensions("topics", shard(TOPIC))
                    assert topics[0]["followers"] == 0
                    await gauge.ingest(
                        Connection(),
                        [event("user_recommendation_states", {}, version=3, operation="T")],
                        generation=4,
                    )
                    feed, _ = await gauge._feed(shard(USER))
                    assert feed[0]["ready"] == 0 and feed[0]["not_needed"] == 1
                    assert (
                        await driver.query("SELECT count() AS n FROM analytics_audience_changes")
                    )[0]["n"] >= 2
            finally:
                await admin.execute(f"DROP DATABASE `{database}` SYNC")

    asyncio.run(run())


@pytest.mark.integration
def test_native_feed_deadlines_ignore_past_candidates_and_publish_expiration(database):
    import psycopg
    from psycopg.rows import dict_row

    settings = _settings()
    dsn = (
        database.kw["bind"]
        .url.render_as_string(hide_password=False)
        .replace("postgresql+psycopg://", "postgresql://", 1)
    )
    # Each populated shard isolates one nullable deadline combination. Shard0
    # also has a later feed, so the aggregate must preserve its earliest expiry.
    cases = [
        (3600, -10, "ready", 3600, 1),
        (-10, 1800, "expired", 1800, 0),
        (3600, None, "ready", 3600, 0),
        (None, 1800, "expired", 1800, 0),
        (None, None, "expired", None, 0),
        (-10, -20, "expired", None, 1),
        (0, 0, "expired", None, 1),
        (1800, 3600, "ready", 1800, 0),
        (3600, 1800, "ready", 1800, 0),
        (-10, None, "expired", None, 0),
        (None, -10, "expired", None, 1),
    ]

    async def run():
        name = f"devfeed_gauge_deadlines_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(settings) as admin:
            await admin.execute(f"CREATE DATABASE `{name}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": name})
                ) as driver:
                    driver.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    gauge = Gauges(settings, driver)
                    await gauge.initialize()
                    stamp = datetime.now(UTC)
                    observed = stamp
                    original_query = driver.query

                    async def query(statement, parameters=None):
                        # Execute the real serving SQL at exact boundaries,
                        # independently of server speed and without wall-clock sleeps.
                        if "now64(6)" in statement:
                            statement = statement.replace(
                                "now64(6)", "{observed:DateTime64(6,'UTC')}"
                            )
                            parameters = {**(parameters or {}), "observed": observed}
                        return await original_query(statement, parameters)

                    driver.query = query
                    users = [uuid.UUID(int=(number << 120) + 1) for number in range(len(cases))]
                    rows = [
                        dict(
                            id=user,
                            interest_count=1,
                            generation=uuid.uuid4(),
                            expires_at=stamp + timedelta(seconds=expires)
                            if expires is not None
                            else None,
                            next_refresh_at=stamp + timedelta(seconds=refresh)
                            if refresh is not None
                            else None,
                            has_recommendations=True,
                        )
                        for user, (expires, refresh, *_rest) in zip(users, cases, strict=True)
                    ]
                    later = uuid.UUID(int=2)
                    rows.append(
                        dict(
                            id=later,
                            interest_count=1,
                            generation=uuid.uuid4(),
                            expires_at=stamp + timedelta(hours=2),
                            next_refresh_at=stamp - timedelta(seconds=10),
                            has_recommendations=True,
                        )
                    )
                    await gauge.ingest(
                        Connection(rows=rows),
                        [event("user_accounts", {"id": str(row["id"])}) for row in rows],
                        generation=1,
                    )
                    missing_feed = uuid.UUID(int=(11 << 120) + 1)
                    account = await gauge._audience_rows(
                        Connection(rows=[{"id": missing_feed}]),
                        {str(missing_feed): (1, stamp)},
                        {str(missing_feed)},
                        1,
                    )
                    await driver.insert(
                        "analytics_audience_changes",
                        [row for row in account if row["kind"] == "account"],
                    )
                    records, transitions = await gauge._feed_many(range(16))
                    for number, (_expires, _refresh, status, seconds, overdue) in enumerate(cases):
                        row = records[number]
                        assert row[status] == (2 if number == 0 else 1), (number, row)
                        assert row["overdue"] == (2 if number == 0 else overdue), (number, row)
                        transition = transitions[number]
                        assert (datetime.fromisoformat(transition) if transition else None) == (
                            stamp + timedelta(seconds=seconds) if seconds is not None else None
                        )
                    assert records[11]["not_needed"] == 1
                    assert transitions[11] is None
                    assert all(
                        records[15][metric] == 0 for metric in records[15] if metric != "shard"
                    )
                    assert transitions[15] is None

                    async with await psycopg.AsyncConnection.connect(
                        dsn, autocommit=True, row_factory=dict_row
                    ) as connection:
                        await connection.execute("TRUNCATE analytics_gauge_refreshes")
                        await connection.execute(
                            "INSERT INTO analytics_gauge_refreshes(domain,shard) "
                            "SELECT 'feed',number FROM generate_series(0,15) number"
                        )
                        assert await gauge.refresh_one(connection, 1)
                        scheduled = await (
                            await connection.execute(
                                "SELECT shard,available_at FROM analytics_gauge_refreshes "
                                "WHERE domain='feed' ORDER BY shard"
                            )
                        ).fetchall()
                        for number, (_expires, _refresh, _status, seconds, _overdue) in enumerate(
                            cases
                        ):
                            available = scheduled[number]["available_at"]
                            if seconds is not None:
                                assert available == stamp + timedelta(seconds=seconds)
                            else:
                                assert available > stamp + timedelta(hours=23)
                        assert scheduled[11]["available_at"] > stamp + timedelta(hours=23)

                        # Simulate the scheduled wake-up at the first expiry.
                        # No source mutation or new capture event is required.
                        observed = stamp + timedelta(hours=1)
                        await connection.execute(
                            "UPDATE analytics_gauge_refreshes SET available_at=clock_timestamp() "
                            "WHERE domain='feed' AND shard=0"
                        )
                        assert await gauge.refresh_one(connection, 2)
                        snapshot = await driver.query(
                            "SELECT ready,expired,overdue FROM analytics_feed_gauges "
                            "WHERE shard=0 AND generation=2"
                        )
                        assert snapshot == [{"ready": 1, "expired": 1, "overdue": 2}]
                        transition = await (
                            await connection.execute(
                                "SELECT available_at FROM analytics_gauge_refreshes "
                                "WHERE domain='feed' AND shard=0"
                            )
                        ).fetchone()
                        assert transition["available_at"] == stamp + timedelta(hours=2)
            finally:
                await admin.execute(f"DROP DATABASE `{name}` SYNC")

    asyncio.run(run())


def test_gauge_schema_retains_raw_revisions_and_expires_only_disposable_generations():
    driver = client()
    asyncio.run(Gauges(None, driver).initialize())
    statements = [call.args[0] for call in driver.execute.await_args_list]
    revisions = [sql for sql in statements if "_changes (" in sql]
    targets = [sql for sql in statements if "_gauges (" in sql]
    assert len(revisions) == 3 and all("TTL" not in sql for sql in revisions)
    assert len(targets) == 4 and all("TTL built_at + INTERVAL 7 DAY" in sql for sql in targets)
    states = [sql for sql in statements if "CREATE MATERIALIZED VIEW" in sql]
    assert len(states) == 3 and all(
        "argMaxState" in sql and "sumState" not in sql for sql in states
    )
    assert all("shard" in sql for sql in states)


@pytest.mark.parametrize("domain", ["feed", "sources", "topics"])
def test_current_population_rebuilds_read_only_one_clickhouse_shard(domain):
    driver = client()
    driver.query.return_value = (
        [{"shard": 17, "ready": 2, "next_transition": None}] if domain == "feed" else []
    )
    gauge = Gauges(None, driver)
    result = asyncio.run(gauge._feed(17) if domain == "feed" else gauge._dimensions(domain, 17))
    assert result == ([{"ready": 2}], None) if domain == "feed" else result == ([], None)
    statement = driver.query.await_args.args[0]
    assert "shard IN {shards:Array(UInt16)}" in statement and "argMaxMerge" in statement
    assert driver.query.await_args.args[1] == {"shards": [17]}
    assert "analytics_fact_events" not in statement and "user_accounts" not in statement
    assert "analytics_gauge_resets" in statement


def test_queue_counts_read_only_indexed_active_work_and_preserve_empty_lanes():
    from devfeed_core.analytics_schema import JOB_KINDS

    rows = [
        dict(kind=kind, queued=0, running=0, oldest_queued_at=None) for kind in JOB_KINDS.values()
    ]
    connection = Connection(rows=rows)
    actual, transition = asyncio.run(Gauges(None, client())._queues(connection))
    assert actual == rows and transition > datetime.now(UTC)
    statement, params = connection.calls[0]
    assert statement.count("WHERE status IN ('queued','running')") == len(JOB_KINDS)
    assert params == tuple(JOB_KINDS.values())
    assert "article_contents" not in statement and "analytics_fact" not in statement


def test_capture_skips_irrelevant_sources_and_resets_only_the_requested_projection():
    driver = client()
    asyncio.run(
        Gauges(None, driver).ingest(
            Connection(),
            [
                event("article_reviews", {"article_id": str(USER)}),
                event("article_reviews", {}, operation="T"),
                event("topics", {}, operation="T"),
            ],
            generation=8,
        )
    )
    driver.insert.assert_awaited_once_with(
        "analytics_gauge_resets",
        [
            dict(kind="topic", delivery_generation=8, event_id=1),
        ],
    )
    driver.insert.reset_mock()
    asyncio.run(Gauges(None, driver).ingest(Connection(), []))
    driver.insert.assert_not_awaited()


def test_capture_keeps_the_latest_source_version_and_handles_deleted_dimensions():
    driver = client()
    row = dict(id=USER, interest_count=0)
    first = event("user_accounts", {"id": str(USER)}, version=9)
    old = event("user_accounts", {"id": str(USER)}, version=1)
    first.pop("created_at")
    old.pop("created_at")
    asyncio.run(
        Gauges(None, driver).ingest(
            Connection(rows=[row]),
            [
                first,
                old,
                event("topics", {"id": str(TOPIC)}, operation="D"),
            ],
        )
    )
    captures = {call.args[0]: call.args[1] for call in driver.insert.await_args_list}
    assert captures["analytics_dimension_changes"][0]["deleted"] == 1
    audience = captures["analytics_audience_changes"]
    assert all(item["event_id"] == 9 and item["captured_at"].year == 1970 for item in audience)


def test_iso_transition_is_parsed_before_cache_renewal_and_source_ack():
    connection = Connection(rows=[dict(domain="feed", shard=1, revision=4)])
    gauge = Gauges(None, client())
    transition = datetime.now(UTC) + timedelta(hours=1)
    gauge._feed_many = AsyncMock(
        return_value=([{"shard": 1, "ready": 1}], {1: transition.isoformat()})
    )
    assert asyncio.run(gauge.refresh_one(connection, 7))
    assert connection.calls[-2][1][2] == [transition]


def test_feed_generation_changes_do_not_rewrite_stable_account_history():
    driver = client()
    row = dict(id=USER, interest_count=2, generation=uuid.uuid4(), has_recommendations=True)
    asyncio.run(
        Gauges(None, driver).ingest(
            Connection(rows=[row]),
            [event("user_recommendation_states", {"user_id": str(USER)})],
            generation=8,
        )
    )
    captured = driver.insert.await_args.args[1]
    assert len(captured) == 1 and captured[0]["kind"] == "feed"
    assert captured[0]["interest_count"] == 2
    driver.insert.reset_mock()
    asyncio.run(
        Gauges(None, driver).ingest(
            Connection(rows=[row]), [event("user_accounts", {"id": str(USER)})], generation=9
        )
    )
    rows = driver.insert.await_args.args[1]
    account = next(row for row in rows if row["kind"] == "account")
    assert account["interest_count"] == account["has_recommendations"] == 0
    assert account["expires_at"] is account["next_refresh_at"] is None
    assert set(account) == set(next(row for row in rows if row["kind"] == "feed"))
    assert account["deleted"] == 0 and account["delivery_generation"] == 9


def test_sixteen_feed_cohorts_share_one_query_and_keep_empty_shards_and_expiries():
    driver = client()
    transition = NOW + timedelta(hours=1)
    driver.query.return_value = [
        {"shard": 0, "ready": 3, "next_transition": transition.isoformat()},
        {"shard": 15, "pending": 2, "next_transition": None},
    ]
    rows, transitions = asyncio.run(Gauges(None, driver)._feed_many(range(16)))
    assert len(rows) == 16 and [row["shard"] for row in rows] == list(range(16))
    assert rows[0]["ready"] == 3 and rows[15]["pending"] == 2
    assert rows[1] == dict(
        shard=1,
        ready=0,
        refreshing=0,
        expired=0,
        pending=0,
        not_needed=0,
        overdue=0,
        empty_with_interests=0,
    )
    assert transitions[0] == transition.isoformat() and transitions[1] is None
    driver.query.assert_awaited_once()
    statement, params = driver.query.await_args.args
    assert params == {"shards": list(range(16))}
    assert "GROUP BY shard" in statement and "a.shard=f.shard" in statement


@pytest.mark.parametrize("numbers", [[], list(range(17)), [1, 1], [-1], [256], ["1"]])
def test_population_aggregation_refuses_unbounded_or_invalid_cohort_sets(numbers):
    driver = client()
    with pytest.raises(ValueError, match="one to sixteen"):
        asyncio.run(Gauges(None, driver)._feed_many(numbers))
    driver.query.assert_not_awaited()


@pytest.mark.parametrize("domain", ["sources", "topics"])
def test_dimension_batches_keep_shard_keys_in_every_relationship_group(domain):
    driver = client()
    driver.query.return_value = []
    records, transitions = asyncio.run(Gauges(None, driver)._dimensions_many(domain, range(16)))
    assert records == [] and transitions == dict.fromkeys(range(16))
    driver.query.assert_awaited_once()
    statement, params = driver.query.await_args.args
    assert params == {"shards": list(range(16))}
    assert "shard IN {shards:Array(UInt16)}" in statement
    if domain == "topics":
        assert "GROUP BY shard,target_id" in statement
        assert "d.shard=f.shard" in statement and "d.shard=i.shard" in statement


@pytest.mark.parametrize("failed_write", [0, 1])
def test_partial_batch_write_never_acknowledges_any_of_its_sixteen_dirty_keys(failed_write):
    connection = Connection(rows=[dict(domain="feed", shard=i, revision=i + 1) for i in range(16)])
    driver = client()
    driver.query.return_value = []
    driver.insert.side_effect = (
        [RuntimeError("sink offline")]
        if failed_write == 0
        else [None, RuntimeError("manifest offline")]
    )
    with pytest.raises(RuntimeError, match="offline"):
        asyncio.run(Gauges(None, driver).refresh_one(connection, 9))
    assert len(connection.calls) == 1
    assert driver.insert.await_count == failed_write + 1
    records = driver.insert.await_args_list[0].args[1]
    assert len(records) == 16 and all(row["ready"] == 0 for row in records)


def test_complete_batch_publishes_one_manifest_then_bulk_revision_and_bootstrap_fences():
    connection = Connection(rows=[dict(domain="feed", shard=i, revision=i + 10) for i in range(16)])
    driver = client()
    driver.query.return_value = []
    assert asyncio.run(Gauges(None, driver).refresh_one(connection, 21))
    assert driver.query.await_count == 1 and driver.insert.await_count == 2
    manifest = driver.insert.await_args_list[1].args[1]
    assert len(manifest) == 16 and {row["shard"] for row in manifest} == set(range(16))
    assert all(row["generation"] == 21 for row in manifest)
    assert len(connection.calls) == 3
    sql, params = connection.calls[1]
    assert "r.revision=done.revision" in sql
    assert params[:2] == (list(range(16)), list(range(10, 26)))
    assert params[3] == "feed"
    assert all(stamp > datetime.now(UTC) + timedelta(hours=23) for stamp in params[2])
    assert connection.calls[2][1] == ("feed", list(range(16)), 21)


@pytest.mark.parametrize("events", [[], [event("article_reviews", {"article_id": str(USER)})]])
def test_irrelevant_or_empty_capture_batches_do_not_schedule_current_gauges(events):
    connection = Connection()
    asyncio.run(Gauges(None, client()).mark_dirty(connection, events))
    assert not connection.calls


@pytest.mark.integration
def test_native_sixteen_shard_batch_keeps_revision_and_bootstrap_races_replay_safe(database):
    import psycopg
    from psycopg.rows import dict_row

    settings = _settings()
    dsn = (
        database.kw["bind"]
        .url.render_as_string(hide_password=False)
        .replace("postgresql+psycopg://", "postgresql://", 1)
    )

    async def run():
        database_name = f"devfeed_gauge_batch_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(settings) as admin:
            await admin.execute(f"CREATE DATABASE `{database_name}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": database_name})
                ) as driver:
                    driver.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    gauge = Gauges(settings, driver)
                    await gauge.initialize()
                    stamp = datetime.now(UTC)
                    users = [uuid.UUID(int=(number << 120) + 1) for number in range(16)]
                    topics = [uuid.UUID(int=(number << 120) + 2) for number in range(16)]
                    sources = [uuid.UUID(int=(number << 120) + 3) for number in range(16)]
                    records = [
                        dict(
                            id=user,
                            interest_count=1,
                            generation=uuid.uuid4(),
                            expires_at=stamp + timedelta(hours=2),
                            next_refresh_at=stamp + timedelta(hours=1),
                            has_recommendations=True,
                        )
                        for user in users
                    ]
                    account_events = [event("user_accounts", {"id": str(user)}) for user in users]
                    for _ in range(2):
                        await gauge.ingest(Connection(rows=records), account_events, generation=1)
                    await gauge.ingest(
                        Connection(rows=records),
                        [
                            event("user_recommendation_states", {"user_id": str(user)}, version=2)
                            for user in users
                        ],
                        generation=2,
                    )
                    dimensions = []
                    for source, topic in zip(sources, topics, strict=True):
                        dimensions.extend(
                            [
                                event(
                                    "sources",
                                    {
                                        "id": str(source),
                                        "name": "Publisher",
                                        "consecutive_failures": 2,
                                    },
                                ),
                                event(
                                    "topics",
                                    {"id": str(topic), "name": "Topic", "status": "active"},
                                ),
                                event(
                                    "user_topics",
                                    {"user_id": str(users[0]), "topic_id": str(topic)},
                                ),
                                event(
                                    "user_interests",
                                    {
                                        "user_id": str(users[-1]),
                                        "topic_id": str(topic),
                                        "reason": "related_topic",
                                    },
                                ),
                            ]
                        )
                    await gauge.ingest(Connection(), dimensions, generation=3)
                    # Delivery replay contributes once, and recommendation-only
                    # changes leave account revision identities untouched.
                    history = await driver.query(
                        "SELECT kind,uniqExact(tuple(shard,event_id,delivery_generation)) AS n "
                        "FROM analytics_audience_changes GROUP BY kind"
                    )
                    assert {row["kind"]: row["n"] for row in history} == {"account": 16, "feed": 32}
                    async with await psycopg.AsyncConnection.connect(
                        dsn, autocommit=True, row_factory=dict_row
                    ) as connection:
                        await connection.execute("TRUNCATE analytics_gauge_refreshes")
                        for index, domain in enumerate(("feed", "sources", "topics")):
                            await connection.execute(
                                "INSERT INTO analytics_gauge_refreshes(domain,shard,"
                                "revision,available_at,"
                                "bootstrap_required,bootstrap_generation) "
                                "SELECT %s,number,number+1,%s,true,9 "
                                "FROM generate_series(0,15) number",
                                (domain, datetime(2001 + index, 1, 1, tzinfo=UTC)),
                            )
                        original = driver.insert
                        raced = False

                        async def insert(table, rows):
                            nonlocal raced
                            await original(table, rows)
                            if table == "analytics_gauge_manifest" and not raced:
                                raced = True
                                await connection.execute(
                                    "UPDATE analytics_gauge_refreshes SET revision=revision+100, "
                                    "available_at='2000-01-01Z',bootstrap_generation=11 "
                                    "WHERE domain='feed' AND shard=1"
                                )

                        driver.insert = insert
                        assert await gauge.refresh_one(connection, 10)
                        states = await (
                            await connection.execute(
                                "SELECT * FROM analytics_gauge_refreshes "
                                "WHERE domain='feed' ORDER BY shard"
                            )
                        ).fetchall()
                        assert states[0]["available_at"] > stamp
                        assert not states[0]["bootstrap_required"]
                        assert states[1]["revision"] == 102 and states[1]["available_at"] < stamp
                        assert states[1]["bootstrap_required"]  # New coverage barrier wins.
                        for generation in (12, 13, 14):
                            assert await gauge.refresh_one(connection, generation)
                        assert not await gauge.refresh_one(connection, 15)
                        remaining = await (
                            await connection.execute(
                                "SELECT count(*) AS n FROM analytics_gauge_refreshes "
                                "WHERE bootstrap_required"
                            )
                        ).fetchone()
                        assert remaining["n"] == 0
                    # The newer manifest replaces the raced shard instead of
                    # adding another copy of its previous generation.
                    total = await driver.query(
                        "SELECT sum(g.ready) AS ready FROM analytics_feed_gauges g "
                        "INNER JOIN (SELECT shard,generation FROM analytics_gauge_manifest FINAL "
                        "WHERE domain='feed') m USING(shard,generation)"
                    )
                    assert total[0]["ready"] == 16
                    source_rows, _ = await gauge._dimensions_many("sources", range(16))
                    topic_rows, _ = await gauge._dimensions_many("topics", range(16))
                    assert len(source_rows) == len(topic_rows) == 16
                    assert all(row["consecutive_failures"] == 2 for row in source_rows)
                    assert all(row["followers"] == row["inferred_users"] == 1 for row in topic_rows)
                    snapshots = await driver.query(
                        "SELECT domain,count() AS n FROM analytics_gauge_manifest FINAL "
                        "GROUP BY domain"
                    )
                    assert {row["domain"]: row["n"] for row in snapshots} == {
                        "feed": 16,
                        "sources": 16,
                        "topics": 16,
                    }
            finally:
                await admin.execute(f"DROP DATABASE `{database_name}` SYNC")

    asyncio.run(run())


@pytest.mark.parametrize(
    "source", ["sources", "topics", "user_accounts", "user_recommendation_states"]
)
def test_identity_corrections_retire_the_old_key_and_schedule_both_shards(source):
    old, new = USER, uuid.UUID("90000000-0000-0000-0000-000000000009")
    key = "user_id" if source == "user_recommendation_states" else "id"
    changed = event(source, {key: str(new), "name": "New", "status": "active"}, version=8)
    changed["before_record"] = {key: str(old), "name": "Old", "status": "active"}
    connection = Connection(rows=[dict(id=new, interest_count=1)])
    driver = client()
    asyncio.run(Gauges(None, driver).ingest(connection, [changed], generation=11))
    rows = driver.insert.await_args.args[1]
    actual = {(row["kind"], row["entity_id"]): row["deleted"] for row in rows}
    if source in {"sources", "topics"}:
        kind = "source" if source == "sources" else "topic"
        assert actual == {(kind, str(old)): 1, (kind, str(new)): 0}
    else:
        assert actual[("feed", str(old))] == 1 and actual[("feed", str(new))] == 0
        if source == "user_accounts":
            assert actual[("account", str(old))] == 1 and actual[("account", str(new))] == 0
        else:
            assert all(kind == "feed" for kind, _ in actual)
        assert connection.calls[0][1] == ([old, new],)
    dirty = Connection()
    asyncio.run(Gauges(None, driver).mark_dirty(dirty, [changed]))
    assert len(dirty.calls) == 1
    domain = "feed" if source in {"user_accounts", "user_recommendation_states"} else source
    assert set(zip(*dirty.calls[0][1], strict=True)) == {(domain, shard(old)), (domain, shard(new))}


@pytest.mark.parametrize("source", ["user_topics", "user_interests"])
@pytest.mark.parametrize("moved", ["user_id", "topic_id"])
def test_membership_primary_key_corrections_preserve_one_edge_and_refresh_affected_feeds(
    source, moved
):
    before = {"user_id": str(USER), "topic_id": str(TOPIC), "reason": "related_topic"}
    record = {**before, moved: "90000000-0000-0000-0000-000000000009"}
    changed = event(source, record, version=9)
    changed["before_record"] = before
    connection = Connection(rows=[dict(id=USER), dict(id=uuid.UUID(record["user_id"]))])
    driver = client()
    asyncio.run(Gauges(None, driver).ingest(connection, [changed], generation=12))
    captured = {call.args[0]: call.args[1] for call in driver.insert.await_args_list}
    edges = captured["analytics_membership_changes"]
    assert len(edges) == 2
    assert (edges[0]["entity_id"], edges[0]["target_id"], edges[0]["deleted"]) == (
        before["user_id"],
        before["topic_id"],
        1,
    )
    assert (edges[1]["entity_id"], edges[1]["target_id"], edges[1]["deleted"]) == (
        record["user_id"],
        record["topic_id"],
        0,
    )
    assert edges[0]["event_id"] == edges[1]["event_id"] == 9
    if moved == "user_id":
        assert len(connection.calls) == 1
        assert connection.calls[0][1] == ([USER, uuid.UUID(record["user_id"])],)
        assert {row["kind"] for row in captured["analytics_audience_changes"]} == {"feed"}
    else:
        assert not connection.calls and "analytics_audience_changes" not in captured
    dirty = Connection()
    asyncio.run(Gauges(None, driver).mark_dirty(dirty, [changed]))
    expected = {("topics", shard(before["topic_id"])), ("topics", shard(record["topic_id"]))}
    if moved == "user_id":
        expected |= {("feed", shard(USER)), ("feed", shard(record["user_id"]))}
    assert set(zip(*dirty.calls[0][1], strict=True)) == expected
    assert len(dirty.calls) == 1


def test_same_identity_reason_correction_writes_only_its_current_tombstone():
    record = {"user_id": str(USER), "topic_id": str(TOPIC), "reason": "explicit_topic"}
    changed = event("user_interests", record)
    changed["before_record"] = {**record, "reason": "related_topic"}
    driver = client()
    connection = Connection()
    asyncio.run(Gauges(None, driver).ingest(connection, [changed]))
    rows = driver.insert.await_args.args[1]
    assert len(rows) == 1 and rows[0]["deleted"] == 1
    assert not connection.calls


@pytest.mark.integration
def test_committed_cross_shard_primary_key_updates_retire_old_gauge_identities(database):
    import psycopg
    from devfeed_core.models import Source, Topic, UserAccount, UserTopic
    from psycopg.rows import dict_row

    user_b, account_c, account_d = [uuid.UUID(int=(number << 120) + 1) for number in (80, 96, 112)]
    topic_b, topic_c, topic_d = [uuid.UUID(int=(number << 120) + 2) for number in (128, 144, 160)]
    source_d = uuid.UUID(int=(176 << 120) + 3)
    with database.begin() as session:
        session.add_all(
            [
                UserAccount(
                    id=value,
                    issuer="https://gauge.invalid",
                    subject=str(value),
                    organization_id="gauge",
                )
                for value in (USER, user_b, account_c)
            ]
        )
        session.add_all(
            [
                Topic(id=value, name="Topic", slug=str(value), kind="language", status="active")
                for value in (TOPIC, topic_b, topic_c)
            ]
        )
        session.add(
            Source(
                id=SOURCE,
                name="Publisher",
                feed_url="https://publisher.example/gauge.xml",
                source_type="publisher",
                approval_status="approved",
                enabled=True,
            )
        )
        session.flush()
        session.add(UserTopic(user_id=USER, topic_id=TOPIC))
    settings = _settings()
    dsn = (
        database.kw["bind"]
        .url.render_as_string(hide_password=False)
        .replace("postgresql+psycopg://", "postgresql://", 1)
    )

    async def run():
        name = f"devfeed_gauge_identity_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(settings) as admin:
            await admin.execute(f"CREATE DATABASE `{name}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": name})
                ) as driver:
                    driver.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    gauge = Gauges(settings, driver)
                    await gauge.initialize()
                    async with await psycopg.AsyncConnection.connect(
                        dsn, autocommit=True, row_factory=dict_row
                    ) as connection:
                        await connection.execute(
                            "DELETE FROM user_recommendation_states WHERE user_id=%s", (user_b,)
                        )
                        baseline = [
                            event("user_accounts", {"id": str(value)})
                            for value in (USER, user_b, account_c)
                        ]
                        baseline += [
                            event("topics", {"id": str(value), "name": "Topic", "status": "active"})
                            for value in (TOPIC, topic_b, topic_c)
                        ]
                        baseline += [
                            event("sources", {"id": str(SOURCE), "name": "Publisher"}),
                            event("user_topics", {"user_id": str(USER), "topic_id": str(TOPIC)}),
                        ]
                        await gauge.ingest(connection, baseline, generation=1)
                        assert (await gauge._dimensions("topics", shard(TOPIC)))[0][0][
                            "followers"
                        ] == 1
                        await connection.execute(
                            "UPDATE analytics_pipeline_state SET enabled=true,"
                            "capture_started_at=clock_timestamp() WHERE singleton"
                        )
                        async with connection.transaction():
                            await connection.execute(
                                "UPDATE user_recommendation_states SET user_id=%s WHERE user_id=%s",
                                (user_b, account_c),
                            )
                            await connection.execute(
                                "UPDATE user_topics SET topic_id=%s "
                                "WHERE user_id=%s AND topic_id=%s",
                                (topic_b, USER, TOPIC),
                            )
                            await connection.execute(
                                "UPDATE user_topics SET user_id=%s "
                                "WHERE user_id=%s AND topic_id=%s",
                                (user_b, USER, topic_b),
                            )
                            await connection.execute(
                                "UPDATE sources SET id=%s WHERE id=%s", (source_d, SOURCE)
                            )
                            await connection.execute(
                                "DELETE FROM topic_relationship_scans WHERE topic_id=%s", (topic_c,)
                            )
                            await connection.execute(
                                "UPDATE topics SET id=%s WHERE id=%s", (topic_d, topic_c)
                            )
                            await connection.execute(
                                "UPDATE user_accounts SET id=%s WHERE id=%s", (account_d, account_c)
                            )
                        changes = await (
                            await connection.execute(
                                "SELECT id,source_table,operation,record,before_record,created_at "
                                "FROM analytics_ingest_events ORDER BY id"
                            )
                        ).fetchall()
                        moved = [
                            row
                            for row in changes
                            if row["before_record"]
                            and any(
                                row["before_record"].get(key) != row["record"].get(key)
                                for key in ("id", "user_id", "topic_id")
                            )
                        ]
                        assert len(moved) == 6
                        assert all(row["operation"] == "I" for row in moved)
                        await connection.execute("TRUNCATE analytics_gauge_refreshes")
                        for _ in range(2):
                            await gauge.ingest(connection, changes, generation=2)
                            await gauge.mark_dirty(connection, changes)
                        dirty = await (
                            await connection.execute(
                                "SELECT domain,shard FROM analytics_gauge_refreshes"
                            )
                        ).fetchall()
                        assert {(row["domain"], row["shard"]) for row in dirty} == {
                            ("topics", shard(TOPIC)),
                            ("topics", shard(topic_b)),
                            ("topics", shard(topic_c)),
                            ("topics", shard(topic_d)),
                            ("sources", shard(SOURCE)),
                            ("sources", shard(source_d)),
                            ("feed", shard(USER)),
                            ("feed", shard(user_b)),
                            ("feed", shard(account_c)),
                            ("feed", shard(account_d)),
                        }
                    rows, _ = await gauge._dimensions_many(
                        "topics", [shard(value) for value in (TOPIC, topic_b, topic_c, topic_d)]
                    )
                    counts = {row["topic_id"]: row["followers"] for row in rows}
                    assert counts == {str(TOPIC): 0, str(topic_b): 1, str(topic_d): 0}
                    rows, _ = await gauge._dimensions_many(
                        "sources", [shard(SOURCE), shard(source_d)]
                    )
                    assert len(rows) == 1 and rows[0]["source_id"] == str(source_d)
                    rows, _ = await gauge._feed_many(
                        [shard(value) for value in (USER, user_b, account_c, account_d)]
                    )
                    health = {row["shard"]: row for row in rows}
                    assert health[shard(USER)]["not_needed"] == 1
                    assert health[shard(user_b)]["pending"] == 1
                    assert all(
                        health[shard(account_c)][metric] == 0
                        for metric in ("ready", "pending", "not_needed")
                    )
                    assert health[shard(account_d)]["not_needed"] == 1
            finally:
                await admin.execute(f"DROP DATABASE `{name}` SYNC")

    asyncio.run(run())
