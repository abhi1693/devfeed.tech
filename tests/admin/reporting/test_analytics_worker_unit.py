"""Fenced native delivery, bootstrap barriers, and bounded publication scheduling."""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from devfeed_core import analytics
from devfeed_core import analytics_worker as worker
from devfeed_core.analytics_schema import BACKFILL_TABLES
from psycopg.errors import LockNotAvailable

NOW = datetime(2026, 10, 26, tzinfo=UTC)
IDENTITY = "11111111-1111-1111-1111-111111111111"


def settings(**changes):
    return NS(
        **(
            dict(
                database_url="postgresql+psycopg://unused/devfeed_test",
                read_model_database_url=None,
                database_connect_timeout_seconds=3,
                analytics_batch_size=2,
                analytics_backfill_batch_size=1,
                analytics_poll_seconds=0.1,
                analytics_backfill_poll_seconds=0.1,
                analytics_rollup_interval_seconds=30,
                clickhouse_timeout_seconds=30,
                clickhouse_max_execution_seconds=20,
            )
            | changes
        )
    )


class Result:
    def __init__(self, value):
        self.value = value

    async def fetchone(self):
        return self.value

    async def fetchall(self):
        return self.value


class Connection:
    def __init__(self, handler=lambda *_: {"ok": 1}):
        self.handler, self.calls, self.active, self.closed = handler, [], 0, False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    @asynccontextmanager
    async def transaction(self):
        self.active += 1
        try:
            yield self
        finally:
            self.active -= 1

    async def execute(self, query, parameters=None):
        query = query.as_string() if hasattr(query, "as_string") else query
        self.calls.append((query, parameters))
        return Result(self.handler(query, parameters))


class Client:
    def __init__(self, connection=None):
        self.settings = settings()
        self.inserts = []
        self.commands = []
        self.queries = []
        self.connection = connection

    async def insert(self, table, rows):
        assert self.connection is None or self.connection.active == 0
        self.inserts.append((table, rows))

    async def execute(self, query, parameters=None):
        assert self.connection is None or self.connection.active == 0
        self.commands.append((query, parameters))

    async def query(self, query, parameters=None):
        assert self.connection is None or self.connection.active == 0
        self.queries.append((query, parameters))
        return []


def exporter(connection=None):
    client = Client(connection)
    result = worker.AnalyticsExporter(client, client.settings)
    result.capture_started_at = NOW
    result.gauges = NS(
        ingest=AsyncMock(),
        mark_dirty=AsyncMock(),
        bootstrap_complete=AsyncMock(return_value=True),
        refresh_one=AsyncMock(return_value=False),
    )
    result.rollups = NS(
        mark_dirty=AsyncMock(),
        refresh_one=AsyncMock(return_value=False),
        compact_one=AsyncMock(return_value=False),
    )
    result.upgrade = NS(
        once=AsyncMock(return_value=False),
        normalize_pending=AsyncMock(side_effect=lambda _connection, events, _generation: events),
        _archive_days=AsyncMock(),
    )
    return result


def run(coroutine):
    return asyncio.run(coroutine)


def test_native_parameter_bindings_and_explicit_read_database():
    assert (
        worker.postgres_dsn(settings(read_model_database_url="postgresql+psycopg://reader"))
        == "postgresql://reader"
    )
    assert worker.postgres_dsn(settings()) == "postgresql://unused/devfeed_test"
    assert analytics._parameter(date(2026, 1, 1)) == "2026-01-01"
    assert analytics._parameter(NOW).endswith("00:00:00.000000")
    assert analytics._parameter({"ids": [1, True, None]}) == {"ids": [1, True, None]}


def test_capture_activation_coalesces_bootstrap_and_retries_without_retaining_partial_locks(
    monkeypatch,
):
    busy, enabled = True, False

    def handle(query, parameters):
        nonlocal busy, enabled
        if query.startswith("SELECT enabled"):
            return {"enabled": enabled}
        if query.startswith("LOCK TABLE") and busy:
            busy = False
            raise LockNotAvailable()
        if query.startswith("UPDATE analytics_pipeline_state SET enabled"):
            enabled = True

    connection = Connection(handle)
    service = exporter(connection)

    async def verify():
        await service._enable_capture(connection)
        count = sum(q.startswith("INSERT") for q, _ in connection.calls)
        assert count == len(BACKFILL_TABLES)
        await service._enable_capture(connection)
        assert sum(q.startswith("INSERT") for q, _ in connection.calls) == count
        assert sum(q.startswith("LOCK") for q, _ in connection.calls) == 2

    run(verify())


def test_activation_timeout_and_missing_or_superseded_lease_fail_closed(monkeypatch):
    service = exporter()
    moments = iter([0, 31])
    monkeypatch.setattr(worker, "time", NS(monotonic=lambda: next(moments, 31)))

    def busy(query, _):
        if query.startswith("SELECT enabled"):
            return {"enabled": False}
        raise LockNotAvailable()

    async def verify():
        with pytest.raises(RuntimeError, match="source writers"):
            await service._enable_capture(Connection(busy))
        with pytest.raises(RuntimeError, match="superseded"):
            await service._check_lease(Connection(lambda *_: None))
        assert await service._lease(Connection(lambda *_: None)) is None
        row = {"capture_started_at": NOW, "native_capture_started_at": None, "lease_generation": 3}
        assert await service._lease(Connection(lambda *_: row)) == row

    run(verify())


@pytest.mark.parametrize("full", [False, True])
def test_bounded_backfill_inserts_generation_one_before_fenced_checkpoint(monkeypatch, full):
    record = {"id": IDENTITY}
    previous = {"id": "00000000-0000-0000-0000-000000000001"}

    def handle(query, parameters):
        if query.startswith("SELECT source_table,cursor"):
            return {"source_table": "articles", "cursor": previous if full else None}
        if "AS record FROM" in query:
            if full:
                assert parameters == [previous["id"], 1] and 'WHERE ("id") > (%s::UUID)' in query
            return [{"record": record}] if full else []
        return {"ok": 1}

    connection = Connection(handle)
    service = exporter(connection)
    facts = [{"domain": "publishing"}]
    transform = AsyncMock(return_value=facts)
    monkeypatch.setattr(worker, "transform_events", transform)
    assert run(service._backfill_one(connection, 20))
    assert service.client.inserts == [("analytics_fact_events", facts)]
    assert transform.call_args.args[2] == 1
    if full:
        assert transform.call_args.args[1][0]["created_at"] == NOW
    service.gauges.ingest.assert_awaited_once_with(
        connection, transform.call_args.args[1], generation=0
    )
    service.rollups.mark_dirty.assert_awaited_once_with(connection, facts, 20)
    update = next(p for q, p in connection.calls if q.startswith("UPDATE analytics_bootstrap"))
    assert update[1] is (not full)
    assert not run(service._backfill_one(Connection(lambda *_: None), 20))


@pytest.mark.parametrize(
    "failure", ["normalize", "facts", "gauges", "dirty", "gauge-dirty", "lease"]
)
def test_delivery_failure_retains_all_events_until_every_projection_is_durable(
    monkeypatch, failure
):
    events = [
        dict(
            id=value,
            source_table="inference_calls",
            operation="I",
            record={},
            before_record=None,
            created_at=NOW,
        )
        for value in (3, 7)
    ]

    def handle(query, _):
        if query.startswith("SELECT id,source"):
            return events
        return None if failure == "lease" else {"ok": 1}

    connection = Connection(handle)
    service = exporter(connection)
    facts = [dict(domain="inference")]
    monkeypatch.setattr(worker, "transform_events", AsyncMock(return_value=facts))
    if failure == "normalize":
        service.upgrade.normalize_pending.side_effect = RuntimeError("legacy audit failed")
    elif failure == "facts":
        service.client.insert = AsyncMock(side_effect=RuntimeError("sink"))
    elif failure == "gauges":
        service.gauges.ingest.side_effect = RuntimeError("sink")
    elif failure == "dirty":
        service.rollups.mark_dirty.side_effect = RuntimeError("sink")
    elif failure == "gauge-dirty":
        service.gauges.mark_dirty.side_effect = RuntimeError("sink")
    with pytest.raises(RuntimeError):
        run(service._export_events(connection, 30))
    assert not any(q.startswith("DELETE") for q, _ in connection.calls)


def test_delivery_acknowledges_exact_ids_only_after_facts_and_gauges(monkeypatch):
    events = [dict(id=n) for n in (3, 9)]
    connection = Connection(lambda q, _: events if q.startswith("SELECT id,source") else {"ok": 1})
    service = exporter(connection)
    transform = AsyncMock(return_value=[])
    monkeypatch.setattr(worker, "transform_events", transform)
    assert run(service._export_events(connection, 42)) == 2
    transform.assert_awaited_once_with(connection, events, 42)
    assert [p for q, p in connection.calls if q.startswith("DELETE")] == [([3, 9],)]
    assert not run(service._export_events(Connection(lambda *_: []), 42))


@pytest.mark.parametrize("normalized", [[], [dict(id=3), dict(id=3), dict(id=9)]])
def test_legacy_delivery_acks_original_ids_and_count_after_normalized_projection(
    monkeypatch, normalized
):
    captured = [dict(id=n) for n in (3, 9)]
    connection = Connection(
        lambda q, _: captured if q.startswith("SELECT id,source") else {"ok": 1}
    )
    service = exporter(connection)
    service.upgrade.normalize_pending = AsyncMock(return_value=normalized)
    transform = AsyncMock(return_value=[])
    monkeypatch.setattr(worker, "transform_events", transform)
    assert run(service._export_events(connection, 42)) == 2
    transform.assert_awaited_once_with(connection, normalized, 42)
    service.gauges.ingest.assert_awaited_once_with(connection, normalized, generation=42)
    assert [p for q, p in connection.calls if q.startswith("DELETE")] == [([3, 9],)]


@pytest.mark.parametrize("full,cursor", [(False, None), (True, {"day": "2026-10-01"})])
def test_daily_backfill_uses_closed_day_keyset_and_fenced_delivery_version(full, cursor):
    records = [dict(day=NOW.date() - timedelta(days=1), metrics={"opens": 7})] if full else []

    def handle(query, parameters):
        if query.startswith("SELECT source_table,cursor"):
            return dict(source_table="overview_daily", cursor=cursor)
        if query.startswith("SELECT day,jsonb_build_object"):
            assert "WHERE day<%s" in query and "ORDER BY day LIMIT %s" in query
            assert parameters == (
                NOW.date(),
                cursor["day"] if cursor else None,
                cursor["day"] if cursor else None,
                1,
            )
            return records
        return {"ok": 1}

    connection = Connection(handle)
    service = exporter(connection)
    assert run(service._backfill_one(connection, 45))
    service.upgrade._archive_days.assert_awaited_once_with(records, NOW, version=45)
    update = next(p for q, p in connection.calls if q.startswith("UPDATE analytics_bootstrap"))
    assert update[1:] == (not full, "overview_daily")
    assert update[0].obj == ({"day": records[-1]["day"].isoformat()} if records else cursor)
    assert not service.client.inserts and not service.gauges.ingest.await_count


@pytest.mark.parametrize("failure", ["archive", "lease"])
def test_daily_backfill_replays_after_archive_or_fence_failure_without_advancing_cursor(failure):
    def handle(query, _):
        if query.startswith("SELECT source_table,cursor"):
            return dict(source_table="overview_daily", cursor=None)
        if query.startswith("SELECT day,jsonb_build_object"):
            return [dict(day=NOW.date() - timedelta(days=1), metrics={"opens": 7})]
        return None if failure == "lease" else {"ok": 1}

    connection = Connection(handle)
    service = exporter(connection)
    if failure == "archive":
        service.upgrade._archive_days.side_effect = RuntimeError("archive unavailable")
    with pytest.raises(RuntimeError):
        run(service._backfill_one(connection, 45))
    assert not any(query.startswith("UPDATE analytics_bootstrap") for query, _ in connection.calls)


@pytest.mark.parametrize(
    "scenario",
    ["raw-incomplete", "legacy-pending", "pending-rollup", "pending-gauge", "ready", "latched"],
)
def test_bootstrap_barrier_and_ready_latch_preserve_original_capture_and_watermark(scenario):
    state = dict(
        sources_complete=scenario != "raw-incomplete",
        native_import_complete=True,
        legacy_complete=scenario != "legacy-pending",
        native_bootstrap_barrier=False,
        native_bootstrap_complete=scenario == "latched",
        facts_complete=scenario != "pending-rollup",
        oldest=NOW - timedelta(seconds=10),
        capture_started_at=NOW,
        native_capture_started_at=NOW - timedelta(days=50),
        reader_coverage_start=None,
    )

    def handle(query, _):
        if query.startswith("SELECT *,NOT EXISTS"):
            return state
        if "native_bootstrap_barrier=true" in query:
            state["native_bootstrap_barrier"] = True
        return {"ok": 1}

    connection = Connection(handle)
    service = exporter(connection)
    service.gauges.bootstrap_complete.return_value = scenario != "pending-gauge"
    lease = {"lease_generation": 42}
    run(service._publish_status(connection, lease, NOW))
    status = service.client.inserts[0][1][0]
    assert status["ready"] == int(scenario in {"ready", "latched"}) and status["version"] == 42
    assert service.bootstrapping is (scenario not in {"ready", "latched"})
    assert status["capture_started_at"] == NOW - timedelta(days=50)
    assert status["reader_coverage_start"] == (NOW - timedelta(days=79)).date()
    assert status["updated_at"] == NOW - timedelta(seconds=10)
    assert service.gauges.bootstrap_complete.await_count == (
        scenario not in {"raw-incomplete", "legacy-pending"}
    )
    if scenario not in {"raw-incomplete", "legacy-pending"}:
        assert any("initial_pending=true" in q for q, _ in connection.calls)


def test_status_heartbeat_is_throttled_but_ready_transition_publishes_immediately():
    state = dict(
        sources_complete=False,
        native_import_complete=False,
        legacy_complete=True,
        native_bootstrap_barrier=True,
        native_bootstrap_complete=False,
        facts_complete=True,
        oldest=None,
        capture_started_at=NOW,
        native_capture_started_at=None,
        reader_coverage_start=NOW.date(),
    )
    connection = Connection(
        lambda q, _: state if q.startswith("SELECT *,NOT EXISTS") else {"ok": 1}
    )
    service = exporter(connection)

    async def verify():
        await service._publish_status(connection, {"lease_generation": 1}, NOW)
        await service._publish_status(connection, {"lease_generation": 2}, NOW)
        assert len(service.client.inserts) == 1
        state.update(sources_complete=True, native_import_complete=True)
        await service._publish_status(connection, {"lease_generation": 3}, NOW)
        assert len(service.client.inserts) == 2 and service.client.inserts[-1][1][0]["ready"] == 1

    run(verify())


@pytest.mark.parametrize(
    "busy", [None, "upgrade", "backfill", "events", "rollup", "gauges", "compact"]
)
def test_export_cycle_orders_handoffs_reuses_original_capture_and_releases_lease(monkeypatch, busy):
    connection = Connection()
    service = exporter(connection)
    trace = []

    def operation(name, value=False):
        async def perform(*_args, **_kwargs):
            trace.append(name)
            return value

        return perform

    service._enable_capture = AsyncMock()
    leases = [
        dict(
            capture_started_at=NOW,
            native_capture_started_at=None if i == 0 else NOW - timedelta(days=60),
            lease_generation=i + 1,
        )
        for i in range(4)
    ]
    service._lease = AsyncMock(side_effect=leases)
    service._check_lease = AsyncMock()
    service._publish_status = AsyncMock()
    service.upgrade.once = AsyncMock(side_effect=operation("upgrade", busy == "upgrade"))
    service._backfill_one = AsyncMock(side_effect=operation("backfill", busy == "backfill"))
    service._export_events = AsyncMock(side_effect=operation("events", int(busy == "events")))
    service.rollups.refresh_one = AsyncMock(side_effect=operation("rollup", busy == "rollup"))
    service._refresh_gauges = AsyncMock(side_effect=operation("gauges", busy == "gauges"))
    service.rollups.compact_one = AsyncMock(side_effect=operation("compact", busy == "compact"))
    monkeypatch.setattr(
        worker.psycopg, "AsyncConnection", NS(connect=AsyncMock(return_value=connection))
    )
    marker = Mock()
    monkeypatch.setattr(worker, "write_runtime_marker", marker)
    assert run(service.once()) is (busy is not None)
    assert trace == ["upgrade", "backfill", "events", "rollup", "gauges", "compact"]
    assert service.capture_started_at == NOW - timedelta(days=60)
    assert service._backfill_one.call_args.args[1] == 2
    assert service._export_events.call_args.args[1] == 3
    assert service._publish_status.call_args.args[1]["lease_generation"] == 4
    assert any(
        q.startswith("UPDATE analytics_pipeline_state SET last_success")
        for q, _ in connection.calls
    )
    assert connection.closed and marker.call_count == 1


@pytest.mark.parametrize("lost", range(4))
def test_export_cycle_stops_after_missing_or_expired_renewal(monkeypatch, lost):
    connection = Connection()
    service = exporter(connection)
    service._enable_capture = AsyncMock()
    lease = dict(capture_started_at=NOW, native_capture_started_at=None, lease_generation=2)
    service._lease = AsyncMock(side_effect=[lease] * lost + [None])
    service._backfill_one = AsyncMock(return_value=False)
    service._export_events = AsyncMock(return_value=0)
    service._refresh_gauges = AsyncMock(return_value=False)
    service._publish_status = AsyncMock()
    monkeypatch.setattr(
        worker.psycopg, "AsyncConnection", NS(connect=AsyncMock(return_value=connection))
    )
    marker = Mock()
    monkeypatch.setattr(worker, "write_runtime_marker", marker)
    if lost == 0:
        assert not run(service.once())
    else:
        with pytest.raises(RuntimeError, match="superseded"):
            run(service.once())
    assert connection.closed and not marker.called
    assert not any(
        q.startswith("UPDATE analytics_pipeline_state SET last_success")
        for q, _ in connection.calls
    )


def test_native_client_rejects_unsafe_tables_and_missing_malformed_or_old_watermarks():
    from devfeed_core.config import Settings

    async def verify():
        options = Settings(
            _env_file=None, database_url="postgresql://unused", redis_url="redis://unused"
        )
        payload = b""

        async def query(*_args, **_kw):
            return payload

        driver = NS(raw_query=query, command=AsyncMock(), raw_insert=AsyncMock(), close=AsyncMock())
        async with analytics.AnalyticsClient(options, driver=driver) as client:
            await client.initialize()
            await client.insert("analytics_fact_events", [])
            with pytest.raises(ValueError):
                await client.insert("untrusted", [{}])
            with pytest.raises(analytics.AnalyticsUnavailable, match="backfill"):
                await client.status()
            payload = b"broken json"
            with pytest.raises(analytics.AnalyticsUnavailable, match="invalid analytical"):
                await client.query("SELECT 1")
            payload = json.dumps(
                dict(ready=1, updated_at="2026-01-01T00:00:00Z", lag_seconds=0)
            ).encode()
            assert (await client.status())["lag_seconds"] > 0

    run(verify())


def test_worker_healthcheck_requires_a_recent_success(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["analytics-worker", "--check"])
    marker = tmp_path / "heartbeat"
    monkeypatch.setattr(worker, "runtime_marker", lambda _: marker)
    with pytest.raises(SystemExit):
        worker.main()
    marker.write_text(str(time.time() - 1000))
    with pytest.raises(SystemExit):
        worker.main()
    marker.write_text(str(time.time()))
    worker.main()


def test_analytical_query_concurrency_stays_within_configured_budget():
    async def verify():
        from devfeed_core.config import Settings

        options = Settings(
            _env_file=None,
            database_url="postgresql://unused",
            redis_url="redis://unused",
            clickhouse_max_concurrent_queries=2,
        )
        active = 0
        maximum = 0

        async def query(*_args, **_kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.001)
            active -= 1
            return b""

        driver = NS(raw_query=query, close=AsyncMock())
        async with analytics.AnalyticsClient(options, driver=driver) as client:
            assert await asyncio.gather(*(client.query("SELECT 1") for _ in range(8))) == [[]] * 8
        assert maximum == 2

    asyncio.run(verify())


def test_official_async_driver_initializes_once_for_concurrent_queries(monkeypatch):
    async def verify():
        from devfeed_core.config import Settings

        options = Settings(
            _env_file=None,
            database_url="postgresql://unused",
            redis_url="redis://unused",
            clickhouse_password="private-driver-password",
            clickhouse_max_concurrent_queries=2,
        )
        driver = NS(raw_query=AsyncMock(return_value=b""), close=AsyncMock())
        factory = AsyncMock(return_value=driver)
        monkeypatch.setattr(analytics.clickhouse_connect, "get_async_client", factory)
        async with analytics.AnalyticsClient(options) as client:
            await asyncio.gather(*(client.query("SELECT 1") for _ in range(8)))
        factory.assert_awaited_once()
        args = factory.call_args.kwargs
        assert args["autogenerate_session_id"] is False
        assert args["connector_limit"] == args["connector_limit_per_host"] == 2
        assert args["connect_timeout"] == options.clickhouse_connect_timeout_seconds
        assert args["send_receive_timeout"] == options.clickhouse_timeout_seconds
        assert args["show_clickhouse_errors"] is False
        driver.close.assert_awaited_once()

    asyncio.run(verify())


def test_query_deadline_covers_admission_and_failed_driver_initialization(monkeypatch):
    async def verify():
        from devfeed_core.config import Settings

        options = Settings(
            _env_file=None,
            database_url="postgresql://unused",
            redis_url="redis://unused",
            clickhouse_timeout_seconds=0.01,
            clickhouse_max_concurrent_queries=1,
        )
        driver = NS(raw_query=AsyncMock(return_value=b""), close=AsyncMock())
        factory = AsyncMock(
            side_effect=[analytics.aiohttp.ClientConnectionError("private detail"), driver]
        )
        monkeypatch.setattr(analytics.clickhouse_connect, "get_async_client", factory)
        async with analytics.AnalyticsClient(options) as client:
            with pytest.raises(analytics.AnalyticsUnavailable, match="ClickHouse request failed"):
                await client.query("SELECT 1")
            assert await client.query("SELECT 1") == []
            await client._semaphore.acquire()
            try:
                with pytest.raises(
                    analytics.AnalyticsUnavailable, match="ClickHouse request failed"
                ):
                    await client.query("SELECT 1")
            finally:
                client._semaphore.release()
        assert factory.await_count == 2
        assert driver.raw_query.await_count == 1
        # A never-used client has no connection or session to dispose.
        await analytics.AnalyticsClient(options).close()

    asyncio.run(verify())


@pytest.mark.parametrize(
    "domain", ["publishing", "audience", "processing", "inference", "sources", "topics"]
)
def test_rollup_rebuild_reads_only_the_requested_cohort_and_publishes_empty_generations(domain):
    from devfeed_core.analytics_rollups import RollupPublisher

    bucket = NOW.replace(hour=3)
    row = dict(domain=domain, bucket=bucket, generation=7)
    connection = Connection(lambda q, _: [row] if q.startswith("WITH oldest") else {"ok": 1})
    client = Client(connection)
    publisher = RollupPublisher(client)
    checks = []

    async def fenced(conn):
        assert conn.active == 1
        checks.append(len(client.inserts))

    assert run(publisher.refresh_one(connection, 20, fence=fenced))
    query, params = client.commands[0]
    assert "analytics_fact_state" in query and "argMaxMerge" in query
    assert "IN {buckets:Array" in query
    assert (
        params["buckets"] == [bucket.strftime("%Y-%m-%d %H:%M:%S")] and params["generation"] == 20
    )
    assert ("toStartOfDay(bucket_hour)" in query) == (domain in {"sources", "topics"})
    assert checks == [0, 1, 1]
    assert client.inserts[0][0] == "analytics_bucket_manifest"
    assert client.inserts[0][1][0]["generation"] == 20
    deletes = [p for q, p in connection.calls if q.startswith("DELETE")]
    assert deletes == [(domain, bucket, 7)]
    assert any(q.startswith("INSERT INTO analytics_rollup_compaction") for q, _ in connection.calls)
    assert not run(publisher.refresh_one(Connection(lambda *_: []), 20, fence=fenced))


@pytest.mark.parametrize("failure", ["rebuild", "manifest", "first-fence", "second-fence"])
def test_failed_rollup_keeps_dirty_generation_until_commit_record_is_durable(failure):
    from devfeed_core.analytics_rollups import RollupPublisher

    row = dict(domain="publishing", bucket=NOW, generation=7)
    connection = Connection(lambda q, _: [row] if q.startswith("WITH oldest") else {"ok": 1})
    client = Client(connection)
    publisher = RollupPublisher(client)
    if failure == "rebuild":
        client.execute = AsyncMock(side_effect=RuntimeError("rebuild failed"))
    if failure == "manifest":
        client.insert = AsyncMock(side_effect=RuntimeError("manifest failed"))
    calls = 0

    async def fenced(conn):
        nonlocal calls
        assert conn.active == 1
        calls += 1
        if (failure == "first-fence" and calls == 1) or (failure == "second-fence" and calls == 2):
            raise RuntimeError("superseded")

    with pytest.raises(RuntimeError):
        run(publisher.refresh_one(connection, 20, fence=fenced))
    assert not any(q.startswith("DELETE") for q, _ in connection.calls)


def test_dirty_cohorts_coalesce_without_postponing_the_first_deadline():
    from devfeed_core.analytics_facts import empty_fact
    from devfeed_core.analytics_rollups import RollupPublisher, rebuild_sql

    connection = Connection()
    client = Client()
    publisher = RollupPublisher(client)
    fact = empty_fact("publishing", IDENTITY, NOW, event_id=1, captured_at=NOW, generation=1)
    run(publisher.mark_dirty(connection, [fact, fact], 4))
    assert len(connection.calls) == 1
    query, parameters = connection.calls[0]
    assert parameters[:2] == (4, 30)
    assert parameters[2] == ["publishing", "sources", "topics"]
    assert len(parameters[3]) == 3
    assert "unnest" in query
    assert "available_at=" not in query.split("DO UPDATE", 1)[1]
    assert "greatest" in query and "least" in query
    with pytest.raises(ValueError, match="Unknown"):
        rebuild_sql("untrusted")
    run(publisher.mark_dirty(connection, [], 4))
    assert len(connection.calls) == 1


@pytest.mark.parametrize(
    "table",
    [
        "publishing_hourly",
        "audience_hourly",
        "processing_hourly",
        "inference_hourly",
        "source_daily",
        "topic_daily",
    ],
)
def test_compaction_uses_private_staging_and_fenced_atomic_partition_replacement(table):
    from devfeed_core.analytics_rollups import RollupPublisher

    row = dict(table_name=table, month=202610, publishes=18)
    connection = Connection(lambda q, _: row if q.startswith("SELECT table_name") else {"ok": 1})
    client = Client(connection)
    publisher = RollupPublisher(client)
    fence = AsyncMock()
    renew = AsyncMock(return_value={"lease_generation": 20})
    assert run(publisher.compact_one(connection, fence=fence, renew=renew))
    assert renew.await_count == 3 and fence.await_count == 4
    create, insert, replace, drop = [q for q, _ in client.commands]
    staging = create.split()[2]
    assert staging.startswith("analytics_compact_" + table + "_")
    assert insert.startswith("INSERT INTO " + staging)
    assert "analytics_bucket_manifest FINAL" in insert and "r.generation=m.generation" in insert
    assert "REPLACE PARTITION" in replace and replace.endswith("FROM " + staging)
    assert drop == "DROP TABLE IF EXISTS " + staging
    assert [
        p for q, p in connection.calls if q.startswith("UPDATE analytics_rollup_compaction")
    ] == [(18, table, 202610)]
    run(publisher.compact_one(connection, fence=fence, renew=renew))
    assert client.commands[4][0].split()[2] != staging
    assert not run(publisher.compact_one(Connection(lambda *_: None), fence=fence, renew=renew))


@pytest.mark.parametrize(
    "failure",
    ["lease-create", "lease-insert", "lease-replace", "create", "insert", "replace", "fence"],
)
def test_compaction_failure_cleans_only_its_own_staging_and_preserves_retry_counter(failure):
    from devfeed_core.analytics_rollups import RollupPublisher

    row = dict(table_name="inference_hourly", month=202610, publishes=16)
    connection = Connection(lambda q, _: row if q.startswith("SELECT table_name") else {"ok": 1})
    client = Client(connection)
    publisher = RollupPublisher(client)
    original = client.execute

    async def execute(query, parameters=None):
        await original(query, parameters)
        if query.startswith(
            {"create": "CREATE", "insert": "INSERT", "replace": "ALTER"}.get(failure, "never")
        ):
            raise RuntimeError("storage failed")

    client.execute = execute
    values = {
        "lease-create": [None],
        "lease-insert": [{}, None],
        "lease-replace": [{}, {}, None],
    }.get(failure, [{}, {}, {}])
    renew = AsyncMock(side_effect=values)
    fenced = AsyncMock(side_effect=RuntimeError("superseded") if failure == "fence" else None)
    with pytest.raises(RuntimeError):
        run(publisher.compact_one(connection, fence=fenced, renew=renew))
    commands = [q for q, _ in client.commands]
    assert commands[-1].startswith("DROP TABLE IF EXISTS analytics_compact_inference_hourly_")
    assert not any(q.startswith("UPDATE analytics_rollup_compaction") for q, _ in connection.calls)
    if failure.startswith("lease-"):
        assert not any("REPLACE PARTITION" in q for q in commands)


@pytest.mark.parametrize(
    "table",
    [
        "publishing_hourly",
        "audience_hourly",
        "processing_hourly",
        "inference_hourly",
        "source_daily",
        "topic_daily",
    ],
)
def test_orphan_cleanup_runs_on_idle_maintenance_cadence_and_drops_only_one_old_copy(
    monkeypatch, table
):
    from devfeed_core import analytics_rollups as rollups

    clock = [100.0]
    monkeypatch.setattr(rollups, "time", NS(monotonic=lambda: clock[0]))
    connection = Connection(lambda *_: None)
    client = Client(connection)
    name = "analytics_compact_" + table + "_" + "a" * 32
    query = client.query

    async def candidate(sql, parameters):
        await query(sql, parameters)
        return [dict(name=name, engine="MergeTree", age_seconds=600)]

    client.query = candidate
    publisher = rollups.RollupPublisher(client)
    fence, renew = AsyncMock(), AsyncMock(return_value={})
    assert run(publisher.compact_one(connection, fence=fence, renew=renew))
    assert client.commands == [("DROP TABLE IF EXISTS " + name, None)]
    assert renew.await_count == fence.await_count == 1
    clock[0] = 399.9
    assert not run(publisher.compact_one(connection, fence=fence, renew=renew))
    assert len(client.queries) == 1 and len(client.commands) == 1
    clock[0] = 400.0
    assert run(publisher.compact_one(connection, fence=fence, renew=renew))
    assert len(client.queries) == 2 and len(client.commands) == 2
    sql, parameters = client.queries[0]
    assert "database=currentDatabase()" in sql and "engine='MergeTree'" in sql
    assert "metadata_modification_time<=now()-toIntervalSecond" in sql
    assert "LIMIT 1 SETTINGS max_execution_time=1" in sql
    assert parameters == {"pattern": rollups.STAGING_PATTERN, "age": 600}


@pytest.mark.parametrize(
    "name,engine,age",
    [
        ("analytics_fact_events", "MergeTree", 900),
        ("analytics_inference_hourly", "MergeTree", 900),
        ("analytics_compact_inference_hourly_" + "a" * 31, "MergeTree", 900),
        ("analytics_compact_inference_hourly_" + "A" * 32, "MergeTree", 900),
        ("analytics_compact_unknown_" + "a" * 32, "MergeTree", 900),
        ("analytics_compact_inference_hourly_" + "a" * 32 + "_extra", "MergeTree", 900),
        ("analytics_compact_inference_hourly_" + "a" * 32, "ReplacingMergeTree", 900),
        ("analytics_compact_inference_hourly_" + "a" * 32, "MergeTree", 599),
        ("analytics_compact_inference_hourly_" + "a" * 32, "MergeTree", 0),
    ],
)
def test_orphan_cleanup_rejects_young_current_malformed_or_nonstaging_tables(name, engine, age):
    from devfeed_core.analytics_rollups import RollupPublisher

    connection = Connection(lambda *_: None)
    client = Client(connection)
    client.query = AsyncMock(return_value=[dict(name=name, engine=engine, age_seconds=age)])
    publisher = RollupPublisher(client)
    fence, renew = AsyncMock(), AsyncMock()
    assert not run(publisher.compact_one(connection, fence=fence, renew=renew))
    assert not client.commands and not renew.await_count and not fence.await_count


@pytest.mark.parametrize("failure", ["lease", "fence", "query", "drop"])
def test_orphan_cleanup_failure_preserves_serving_tables_and_throttles_catalog_retries(
    monkeypatch, failure
):
    from devfeed_core import analytics_rollups as rollups

    monkeypatch.setattr(rollups, "time", NS(monotonic=lambda: 100.0))
    connection = Connection(lambda *_: None)
    client = Client(connection)
    name = "analytics_compact_inference_hourly_" + "a" * 32
    client.query = AsyncMock(return_value=[dict(name=name, engine="MergeTree", age_seconds=900)])
    if failure == "query":
        client.query.side_effect = RuntimeError("catalog unavailable")
    if failure == "drop":
        client.execute = AsyncMock(side_effect=RuntimeError("drop failed"))
    fence = AsyncMock(side_effect=RuntimeError("superseded") if failure == "fence" else None)
    renew = AsyncMock(return_value=None if failure == "lease" else {})
    publisher = rollups.RollupPublisher(client)
    with pytest.raises(RuntimeError):
        run(publisher.compact_one(connection, fence=fence, renew=renew))
    if failure != "drop":
        assert not client.commands
    else:
        client.execute.assert_awaited_once_with("DROP TABLE IF EXISTS " + name)
    assert not run(publisher.compact_one(connection, fence=fence, renew=renew))
    assert client.query.await_count == 1


@pytest.mark.integration
def test_real_orphan_cleanup_preserves_raw_and_serving_facts(monkeypatch):
    import uuid

    from devfeed_core import analytics_rollups as rollups
    from devfeed_core.analytics_transform import normalize_event

    from tests.support import analytics as fixtures

    configuration = fixtures.settings()
    database = "orphan_" + uuid.uuid4().hex[:12] + "_test"

    async def verify():
        async with analytics.AnalyticsClient(configuration) as admin:
            await admin.execute(f"CREATE DATABASE `{database}` ENGINE=Atomic")
            try:
                async with analytics.AnalyticsClient(
                    configuration.model_copy(update={"clickhouse_database": database})
                ) as client:
                    await client.initialize()
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    event = dict(
                        id=1,
                        source_table="inference_calls",
                        operation="I",
                        created_at=NOW,
                        record=dict(id=IDENTITY, started_at=NOW.isoformat(), status="succeeded"),
                    )
                    await client.insert("analytics_fact_events", normalize_event(event, 1))
                    await client.execute(
                        rollups.rebuild_sql("inference"),
                        {"bucket": NOW, "end": NOW + timedelta(hours=1), "generation": 1},
                    )
                    old = "analytics_compact_inference_hourly_" + "0" * 32
                    young = "analytics_compact_inference_hourly_" + "f" * 32
                    malformed = "analytics_compact_inference_hourly_bad"
                    for name in (old, young, malformed):
                        await client.execute(f"CREATE TABLE {name} AS analytics_inference_hourly")
                        await client.execute(
                            f"INSERT INTO {name} SELECT * FROM analytics_inference_hourly"
                        )
                    connection = Connection(lambda *_: None)
                    publisher = rollups.RollupPublisher(client)
                    renew = AsyncMock(return_value={})

                    async def fenced(conn):
                        assert conn.active == 1

                    assert not await publisher.compact_one(connection, fence=fenced, renew=renew)
                    # Test-only threshold injection simulates an old orphan;
                    # production always requires age >= 600 seconds.
                    monkeypatch.setattr(rollups, "ORPHAN_MIN_AGE_SECONDS", 0)
                    publisher._next_orphan_check = 0
                    assert await publisher.compact_one(connection, fence=fenced, renew=renew)
                    remaining = await client.query(
                        "SELECT name FROM system.tables WHERE database=currentDatabase() "
                        "AND startsWith(name,'analytics_compact_') ORDER BY name"
                    )
                    assert remaining == [{"name": malformed}, {"name": young}]
                    for table in ("analytics_fact_events", "analytics_inference_hourly"):
                        assert await client.query(f"SELECT sum(calls) AS calls FROM {table}") == [
                            {"calls": 1}
                        ]
                    assert renew.await_count == 1
            finally:
                await admin.execute(f"DROP DATABASE `{database}` SYNC")

    run(verify())


@pytest.mark.parametrize(
    "domain", ["publishing", "audience", "processing", "inference", "sources", "topics"]
)
def test_single_cohort_rebuild_remains_time_prunable_for_manual_fixture_builds(domain):
    from devfeed_core.analytics_rollups import rebuild_sql

    query = rebuild_sql(domain)
    assert "bucket_hour >= {bucket:" in query and "bucket_hour < {end:" in query
    assert "IN {buckets:" not in query


def test_rollup_batch_is_bounded_domain_scoped_and_preserves_racing_dirty_revisions():
    from devfeed_core.analytics_rollups import REFRESH_BATCH_SIZE, RollupPublisher

    rows = [
        dict(domain="publishing", bucket=NOW + timedelta(days=n), generation=7) for n in range(33)
    ]
    selected = rows[:REFRESH_BATCH_SIZE]
    pending = {(row["domain"], row["bucket"]): row["generation"] for row in rows}
    other = ("audience", NOW)
    pending[other] = 5

    def handle(query, parameters):
        if query.startswith("WITH oldest"):
            assert "domain=(SELECT domain FROM oldest)" in query
            assert parameters[-2] == 1
            assert parameters[-1] == REFRESH_BATCH_SIZE == 32
            return selected
        if query.startswith("DELETE FROM analytics_fact_refresh"):
            domain, bucket, revision = parameters
            key = (domain, bucket)
            if pending[key] <= revision:
                del pending[key]
        return {"ok": 1}

    connection = Connection(handle)
    client = Client(connection)
    original = client.insert

    async def manifest(table, records):
        await original(table, records)
        pending[("publishing", rows[0]["bucket"])] = 99

    client.insert = manifest
    assert run(RollupPublisher(client).refresh_one(connection, 20, fence=AsyncMock()))
    assert len(client.inserts[0][1]) == 32
    assert client.commands[0][1]["buckets"] == [
        row["bucket"].strftime("%Y-%m-%d %H:%M:%S") for row in selected
    ]
    assert pending == {
        ("publishing", rows[0]["bucket"]): 99,
        ("publishing", rows[-1]["bucket"]): 7,
        other: 5,
    }
    counters = [
        parameters
        for query, parameters in connection.calls
        if query.startswith("INSERT INTO analytics_rollup_compaction")
    ]
    assert counters == [("publishing_hourly", 202610), ("publishing_hourly", 202611)]


@pytest.mark.parametrize(
    "scenario,expected_calls,expected_busy",
    [
        ("empty", 1, False),
        ("bounded", 16, True),
        ("deadline", 1, True),
        ("drained", 3, True),
    ],
)
def test_gauge_cycle_has_independent_count_time_and_empty_queue_bounds(
    monkeypatch, scenario, expected_calls, expected_busy
):
    connection = Connection()
    service = exporter(connection)
    ticks = iter([0, 0.26]) if scenario == "deadline" else iter([])
    monkeypatch.setattr(worker, "time", NS(monotonic=lambda: next(ticks, 0)))
    if scenario == "empty":
        service.gauges.refresh_one.return_value = False
    elif scenario == "drained":
        service.gauges.refresh_one.side_effect = [True, True, False]
    else:
        service.gauges.refresh_one.return_value = True
    service._lease = AsyncMock(side_effect=[{"lease_generation": n + 1} for n in range(16)])
    assert run(service._refresh_gauges(connection)) is expected_busy
    assert service.gauges.refresh_one.await_count == expected_calls
    assert [call.args for call in service.gauges.refresh_one.await_args_list] == [
        (connection, n + 1) for n in range(expected_calls)
    ]


@pytest.mark.parametrize("completed", [0, 1])
def test_gauge_cycle_renews_each_manifest_generation_and_stops_when_lease_is_lost(
    monkeypatch, completed
):
    connection = Connection()
    service = exporter(connection)
    service._lease = AsyncMock(side_effect=[{"lease_generation": 10}] * completed + [None])
    service.gauges.refresh_one.return_value = True
    monkeypatch.setattr(worker, "time", NS(monotonic=lambda: 0))
    with pytest.raises(RuntimeError, match="superseded"):
        run(service._refresh_gauges(connection))
    assert service.gauges.refresh_one.await_count == completed
    assert service._lease.await_count == completed + 1
