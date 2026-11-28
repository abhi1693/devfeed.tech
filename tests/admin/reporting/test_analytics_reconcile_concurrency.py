"""Real PostgreSQL publication admission and snapshot connection lifecycles."""

import asyncio
import threading
import uuid
import weakref
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from devfeed_core.analytics import AnalyticsClient, ReconciliationBusy
from devfeed_core.analytics_connections import postgres_connections
from devfeed_core.analytics_reconcile import PUBLISH_LOCK, CurrentStateReconciler
from devfeed_core.config import get_settings
from psycopg import AsyncConnection, sql
from psycopg.errors import QueryCanceled
from psycopg.rows import dict_row
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from tests.support.article_readiness import processing_case as processing_case

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


@pytest.fixture
def current_card_source(integration_environment, processing_case):
    from devfeed_core import analysis
    from devfeed_core.db import get_engine
    from devfeed_core.models import Article, Source, Topic
    from devfeed_core.urls import fingerprint

    article, facts = processing_case
    source = article.origins[0].source
    topic = article.topic_links[0].topic
    source.feed_url = f"https://{source.id}.invalid/feed"
    article.canonical_url = f"https://{source.id}.invalid/{article.id}"
    article.url_hash = fingerprint(article.canonical_url)
    for origin in article.origins:
        origin.entry_key = str(article.id)
        origin.original_url = article.canonical_url
    for link in article.topic_links:
        link.evidence = "Angular routing"
    facts["enrichment"].result = {"input_url": article.canonical_url, "image_url": None}
    engine = get_engine()
    with Session(engine, expire_on_commit=False) as session:
        session.add(article)
        session.flush()
        taxonomy = analysis.catalog(session)
        snapshot = analysis.source_snapshot(article, None)
        job = facts["analysis"]
        job.input_snapshot = snapshot
        job.input_hash = analysis.snapshot_hash(snapshot)
        job.catalog_snapshot = analysis.analysis_candidates(taxonomy, snapshot)
        job.catalog_hash = analysis.snapshot_hash(job.catalog_snapshot)
        article.classification_provenance = {
            **article.classification_provenance,
            "input_hash": job.input_hash,
        }
        session.add_all([job, facts["enrichment"]])
        session.commit()
    value = SimpleNamespace(
        article_id=article.id,
        job_id=job.id,
        topic_id=topic.id,
        input_hash=job.input_hash,
        engine=engine,
    )
    try:
        yield value
    finally:
        with engine.begin() as connection:
            connection.execute(delete(Article).where(Article.id == value.article_id))
            connection.execute(delete(Source).where(Source.id == source.id))
            connection.execute(delete(Topic).where(Topic.id == value.topic_id))


def test_native_builder_reuses_only_revision_valid_catalogs_and_recomputes_job_readiness(
    current_card_source, monkeypatch
):
    from devfeed_core import analysis
    from devfeed_core.models import ArticleAnalysisJob, Topic

    source = current_card_source
    reader = CurrentStateReconciler(AnalyticsClient(get_settings()), get_settings())
    original = analysis._read_catalog
    loads = []
    previous = None

    def load(session):
        if previous is not None and len(loads) == 1:
            assert previous() is None and not reader._card_ranking
        loads.append(threading.get_ident())
        return original(session)

    def change(model, identity, **values):
        with source.engine.begin() as connection:
            connection.execute(update(model).where(model.id == identity).values(**values))

    monkeypatch.setattr(analysis, "_read_catalog", load)

    async def scenario():
        nonlocal previous
        identifiers = [str(source.article_id)]
        [first] = await reader._build("article", identifiers, 1)
        assert first["stage"] == "ready"
        [second] = await reader._build("article", identifiers, 2)
        assert second["semantic_hash"] == first["semantic_hash"] and len(loads) == 1
        assert loads[0] != threading.get_ident()
        previous = weakref.ref(reader._card_ranking[0])
        await asyncio.to_thread(change, ArticleAnalysisJob, source.job_id, input_hash="0" * 64)
        [changed] = await reader._build("article", identifiers, 3)
        assert changed["stage"] == "analysis" and changed["semantic_hash"] != first["semantic_hash"]
        assert len(loads) == 1 and previous() is reader._card_ranking[0]
        await asyncio.to_thread(
            change, ArticleAnalysisJob, source.job_id, input_hash=source.input_hash
        )
        [restored] = await reader._build("article", identifiers, 4)
        assert restored["semantic_hash"] == first["semantic_hash"]
        await asyncio.to_thread(change, Topic, source.topic_id, name="Renamed Angular")
        [catalog_changed] = await reader._build("article", identifiers, 5)
        assert catalog_changed["stage"] == "analysis" and len(loads) == 2
        assert len(reader._card_catalogs) == len(reader._card_ranking) == 1
        assert len(reader._card_ranking[0].results) <= 8
        other = CurrentStateReconciler(AnalyticsClient(get_settings()), get_settings())
        [independent] = await other._build("article", identifiers, 6)
        assert independent["semantic_hash"] == catalog_changed["semantic_hash"]
        assert len(loads) == 3 and other._card_ranking[0] is not reader._card_ranking[0]

    asyncio.run(scenario())


def test_native_catalog_loader_failure_discards_the_previous_prepared_generation(
    current_card_source, monkeypatch
):
    from devfeed_core import analysis
    from devfeed_core.models import Topic

    source = current_card_source
    reader = CurrentStateReconciler(AnalyticsClient(get_settings()), get_settings())
    original = analysis._read_catalog

    def fail(_session):
        assert not reader._card_catalogs and not reader._card_ranking
        raise ValueError("Controlled catalog loader failure")

    async def scenario():
        identifiers = [str(source.article_id)]
        await reader._build("article", identifiers, 1)
        previous = weakref.ref(reader._card_ranking[0])

        def change():
            with source.engine.begin() as connection:
                connection.execute(
                    update(Topic).where(Topic.id == source.topic_id).values(name="Renamed Angular")
                )

        await asyncio.to_thread(change)
        monkeypatch.setattr(analysis, "_read_catalog", fail)
        with pytest.raises(ValueError, match="Controlled catalog loader"):
            await reader._build("article", identifiers, 2)
        assert previous() is None and not reader._card_catalogs and not reader._card_ranking
        monkeypatch.setattr(analysis, "_read_catalog", original)
        [retried] = await reader._build("article", identifiers, 3)
        assert retried["stage"] == "analysis"

    asyncio.run(scenario())


def test_timed_out_native_catalog_builder_retains_exclusive_ownership_until_completion(
    current_card_source, monkeypatch
):
    from devfeed_core import analytics_direct_cards

    source = current_card_source
    reader = CurrentStateReconciler(AnalyticsClient(get_settings()), get_settings())
    original = analytics_direct_cards.build_cards
    started, release = threading.Event(), threading.Event()
    calls = []

    def blocked(*args):
        calls.append(threading.get_ident())
        started.set()
        if not release.wait(5):
            raise AssertionError("Test did not release the native builder")
        return original(*args)

    async def scenario():
        identifiers = [str(source.article_id)]
        [first] = await reader._build("article", identifiers, 1)
        ranking = reader._card_ranking[0]
        monkeypatch.setattr(analytics_direct_cards, "build_cards", blocked)
        task = asyncio.create_task(reader._build("article", identifiers, 2))
        try:
            async with asyncio.timeout(3):
                while not started.is_set():
                    await asyncio.sleep(0.001)
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.02):
                    await task
            await reader.close()
            with pytest.raises(ReconciliationBusy) as busy:
                await reader._build("article", identifiers, 3)
            assert busy.value.reason_code == "builder_busy"
            assert len(calls) == 1 and reader._card_ranking[0] is ranking
            release.set()
            await asyncio.wait_for(reader._builder, 3)
            [retry] = await reader._build("article", identifiers, 4)
            assert len(calls) == 2 and reader._card_ranking[0] is ranking
            assert retry["semantic_hash"] == first["semantic_hash"]
        finally:
            release.set()
            await cancel_tasks([task])

    asyncio.run(scenario())
