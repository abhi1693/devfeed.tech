"""Real PostgreSQL publication admission and snapshot connection lifecycles."""

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from devfeed_core.analytics import AnalyticsClient, ReconciliationBusy
from devfeed_core.analytics_connections import postgres_connections
from devfeed_core.analytics_reconcile import PUBLISH_LOCK, CurrentStateReconciler
from devfeed_core.config import get_settings
from psycopg import AsyncConnection, sql
from psycopg.errors import QueryCanceled
from psycopg.rows import dict_row

pytestmark = pytest.mark.integration


class SourceConnections:
    """Record real source sessions and optionally pause a completed fence exchange."""

    def __init__(self, *, pause_after_fence=False):
        self.connections = []
        self.pids = []
        self.connected = asyncio.Event()
        self.fenced = asyncio.Event()
        self.resume = asyncio.Event()
        self.pause_after_fence = pause_after_fence

    @asynccontextmanager
    async def __call__(self):
        owner = self

        class Connection(AsyncConnection):
            async def execute(self, query, params=None, **kwargs):
                cursor = await super().execute(query, params, **kwargs)
                # The query has completed on PostgreSQL. Pausing the response
                # lets cancellation hit the narrow acquired-but-not-yet-yielded
                # window without replacing the driver, server, or lock behavior.
                if owner.pause_after_fence and query == "SELECT pg_advisory_lock(%s,%s)":
                    owner.fenced.set()
                    await owner.resume.wait()
                return cursor

        dsn = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        async with await Connection.connect(
            dsn, autocommit=True, row_factory=dict_row
        ) as connection:
            self.connections.append(connection)
            self.pids.append(connection.info.backend_pid)
            self.connected.set()
            yield connection


@asynccontextmanager
async def reconciler(*, pause_after_fence=False):
    settings = get_settings()
    connections = SourceConnections(pause_after_fence=pause_after_fence)
    async with AnalyticsClient(settings) as client:
        instance = CurrentStateReconciler(client, settings, connections)
        try:
            yield instance, connections
        finally:
            await instance.close()


async def lock_state(observer, pid):
    return await (
        await observer.execute(
            "SELECT granted FROM pg_locks WHERE pid=%s AND locktype='advisory' "
            "AND classid::bigint=%s AND objid::bigint=%s AND objsubid=2",
            (pid, *PUBLISH_LOCK),
        )
    ).fetchone()


async def wait_for_lock(observer, pid, *, granted=False):
    async with asyncio.timeout(3):
        while True:
            row = await lock_state(observer, pid)
            if row is not None and row["granted"] is granted:
                return
            await asyncio.sleep(0.005)


async def assert_session_closed(observer, connection, pid):
    assert connection.closed
    async with asyncio.timeout(3):
        while await (
            await observer.execute("SELECT 1 FROM pg_stat_activity WHERE pid=%s", (pid,))
        ).fetchone():
            await asyncio.sleep(0.005)
    assert await lock_state(observer, pid) is None


async def read_value(connection, table):
    row = await (await connection.execute(sql.SQL("SELECT value FROM {}").format(table))).fetchone()
    assert row is not None
    return row["value"]


async def cancel_tasks(tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("publisher_delay", [0, 0.75], ids=["short-queue", "six-second-queue"])
def test_queued_publishers_admit_reconciliation_before_later_writers(
    integration_environment, publisher_delay
):
    async def scenario():
        connect = postgres_connections(get_settings().database_url)
        async with connect() as observer, connect() as holder, reconciler() as (reader, sources):
            table = sql.Identifier("reconcile_concurrency_" + uuid.uuid4().hex)
            await observer.execute(
                sql.SQL("CREATE TABLE {} (value integer NOT NULL)").format(table)
            )
            await observer.execute(sql.SQL("INSERT INTO {} VALUES (0)").format(table))
            await holder.execute("SELECT pg_advisory_lock(%s,%s)", PUBLISH_LOCK)
            tasks = []
            source_ready, later_admitted, later_finished = (
                asyncio.Event(),
                asyncio.Event(),
                asyncio.Event(),
            )
            release_later = asyncio.Event()
            snapshots = []
            heartbeats = 0
            heartbeat_ready = asyncio.Event()

            async def heartbeat():
                nonlocal heartbeats
                while True:
                    heartbeats += 1
                    if heartbeats >= 10:
                        heartbeat_ready.set()
                    await asyncio.sleep(0.005)

            async def publish(connection, value, *, later=False):
                # This is the same transaction-scoped fence used by direct
                # publishers, with a private row making commit visibility visible.
                async with connection.transaction():
                    await connection.execute("SET LOCAL statement_timeout='10s'")
                    await connection.execute("SELECT pg_advisory_xact_lock(%s,%s)", PUBLISH_LOCK)
                    await connection.execute(
                        sql.SQL("UPDATE {} SET value=%s").format(table), (value,)
                    )
                    if later:
                        later_admitted.set()
                        await release_later.wait()
                    elif publisher_delay:
                        # Model the real network-bound publication work while
                        # PostgreSQL retains this transaction-scoped fence.
                        await asyncio.sleep(publisher_delay)

            async def start_publisher(value, *, later=False):
                context = connect()
                connection = await context.__aenter__()

                async def run():
                    try:
                        await publish(connection, value, later=later)
                    finally:
                        await context.__aexit__(None, None, None)

                task = asyncio.create_task(run())
                tasks.append(task)
                await wait_for_lock(observer, connection.info.backend_pid)
                return task

            async def read_snapshot():
                async with reader._source_snapshot() as (connection, revision, observed_at):
                    assert revision > 0 and observed_at.tzinfo is not None
                    timeout = await (await connection.execute("SHOW statement_timeout")).fetchone()
                    assert timeout["statement_timeout"] == "5s"
                    snapshots.append(await read_value(connection, table))
                    source_ready.set()
                    await later_finished.wait()
                    snapshots.append(await read_value(connection, table))

            try:
                # Eight earlier writers and eight later writers leave a
                # continuous server-side queue throughout the source admission.
                earlier = [await start_publisher(value) for value in range(1, 9)]
                source = asyncio.create_task(read_snapshot())
                tasks.append(source)
                await asyncio.wait_for(sources.connected.wait(), 3)
                await wait_for_lock(observer, sources.connections[0].info.backend_pid)
                later = [await start_publisher(value, later=True) for value in range(9, 17)]
                pulse = asyncio.create_task(heartbeat())
                tasks.append(pulse)
                await asyncio.wait_for(heartbeat_ready.wait(), 1)
                assert not source.done(), "The source must still be waiting behind the holder"
                pulses_before_release = heartbeats
                await holder.execute("SELECT pg_advisory_unlock(%s,%s)", PUBLISH_LOCK)
                await asyncio.wait_for(source_ready.wait(), 9)
                assert heartbeats - pulses_before_release >= publisher_delay * 8 / 0.05
                await asyncio.wait_for(later_admitted.wait(), 3)
                assert snapshots == [8], "The snapshot must include every earlier publisher commit"
                assert not all(task.done() for task in later), "Later publishers must remain queued"
                release_later.set()
                await asyncio.wait_for(asyncio.gather(*earlier, *later), 3)
                assert await read_value(observer, table) == 16
                later_finished.set()
                await asyncio.wait_for(source, 3)
                assert snapshots == [8, 8], "Later commits must not change the repeatable-read view"
                assert heartbeats >= 10
                await assert_session_closed(observer, sources.connections[0], sources.pids[0])
            finally:
                release_later.set()
                later_finished.set()
                await cancel_tasks(tasks)
                await holder.execute("SELECT pg_advisory_unlock_all()")
                await observer.execute(sql.SQL("DROP TABLE {}").format(table))

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["waiting", "fenced", "snapshot"])
def test_cancelled_source_closes_session_and_releases_fence(integration_environment, phase):
    async def scenario():
        connect = postgres_connections(get_settings().database_url)
        async with (
            connect() as observer,
            connect() as holder,
            reconciler(pause_after_fence=phase == "fenced") as (reader, sources),
        ):
            await holder.execute("SELECT pg_advisory_lock(%s,%s)", PUBLISH_LOCK)
            entered = asyncio.Event()

            async def source():
                async with reader._source_snapshot():
                    entered.set()
                    await asyncio.Event().wait()

            task = asyncio.create_task(source())
            try:
                await asyncio.wait_for(sources.connected.wait(), 3)
                connection = sources.connections[0]
                await wait_for_lock(observer, connection.info.backend_pid)
                if phase != "waiting":
                    await holder.execute("SELECT pg_advisory_unlock(%s,%s)", PUBLISH_LOCK)
                    ready = sources.fenced if phase == "fenced" else entered
                    await asyncio.wait_for(ready.wait(), 3)
                if phase == "fenced":
                    await wait_for_lock(observer, connection.info.backend_pid, granted=True)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
                await assert_session_closed(observer, connection, sources.pids[0])
                if phase == "waiting":
                    await wait_for_lock(observer, holder.info.backend_pid, granted=True)
                    await holder.execute("SELECT pg_advisory_unlock(%s,%s)", PUBLISH_LOCK)
                sources.pause_after_fence = False
                async with reader._source_snapshot() as (retry, revision, _observed_at):
                    assert revision > 0
                    assert (await (await retry.execute("SELECT 1 AS value")).fetchone())[
                        "value"
                    ] == 1
                await assert_session_closed(observer, sources.connections[-1], sources.pids[-1])
            finally:
                await cancel_tasks([task])
                await holder.execute("SELECT pg_advisory_unlock_all()")

    asyncio.run(scenario())


def test_source_timeout_releases_session_and_retry_progresses(integration_environment):
    async def scenario():
        connect = postgres_connections(get_settings().database_url)
        async with connect() as observer, connect() as holder, reconciler() as (reader, sources):
            await holder.execute("SELECT pg_advisory_lock(%s,%s)", PUBLISH_LOCK)

            async def source():
                async with reader._source_snapshot():
                    pytest.fail("The source cannot enter while the other publisher holds its fence")

            task = asyncio.create_task(source())
            try:
                await asyncio.wait_for(sources.connected.wait(), 3)
                connection = sources.connections[0]
                await wait_for_lock(observer, connection.info.backend_pid)
                # Exercise the actual ten-second admission timeout; a
                # synthetic driver exception would miss cancellation cleanup.
                with pytest.raises(ReconciliationBusy) as error:
                    await asyncio.wait_for(task, 12)
                assert error.value.reason_code == "publication_busy"
                assert isinstance(error.value.__cause__, QueryCanceled)
                assert error.value.__cause__.sqlstate == "57014"
                await assert_session_closed(observer, connection, sources.pids[0])
                await wait_for_lock(observer, holder.info.backend_pid, granted=True)
                await holder.execute("SELECT pg_advisory_unlock(%s,%s)", PUBLISH_LOCK)
                async with reader._source_snapshot() as (retry, revision, _observed_at):
                    assert revision > 0
                    assert (await (await retry.execute("SELECT 1 AS value")).fetchone())[
                        "value"
                    ] == 1
                await assert_session_closed(observer, sources.connections[-1], sources.pids[-1])
            finally:
                await cancel_tasks([task])
                await holder.execute("SELECT pg_advisory_unlock_all()")

    asyncio.run(scenario())
