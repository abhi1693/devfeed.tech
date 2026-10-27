"""Native PostgreSQL delivery and ClickHouse current-card lifecycle regressions."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from devfeed_admin_api.analytics_runtime import AnalyticsRuntime
from devfeed_admin_api.overview_windows import recent_window
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_cards import (
    REFRESH_DUE_SQL,
    TIMER_DUE_SQL,
    CardPublisher,
    normalize_card,
)
from devfeed_core.analytics_worker import AnalyticsExporter
from devfeed_core.models import Article, ArticleEnrichmentJob, ArticleOrigin, InferenceCall, Source
from fastapi import HTTPException
from psycopg.rows import dict_row
from sqlalchemy import text

from tests.support.analytics import settings

pytestmark = pytest.mark.integration


@asynccontextmanager
async def native_publisher():
    configured = settings()
    name = f"devfeed_cards_{uuid.uuid4().hex}_test"
    async with AnalyticsClient(configured) as admin:
        await admin.execute(f"CREATE DATABASE `{name}`")
        try:
            async with AnalyticsClient(
                configured.model_copy(update={"clickhouse_database": name})
            ) as client:
                client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                publisher = CardPublisher(client, configured)
                dsn = configured.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
                async with await psycopg.AsyncConnection.connect(
                    dsn, autocommit=True, row_factory=dict_row
                ) as connection:
                    token = uuid.uuid4()
                    await connection.execute(
                        "UPDATE analytics_pipeline_state SET enabled=true,native_lease_token=%s,"
                        "lease_until=clock_timestamp()+interval '1 hour' WHERE singleton",
                        (token,),
                    )

                    async def fence(connection):
                        row = await (
                            await connection.execute(
                                "SELECT 1 FROM analytics_pipeline_state WHERE singleton "
                                "AND native_lease_token=%s "
                                "AND lease_until>clock_timestamp() FOR UPDATE",
                                (token,),
                            )
                        ).fetchone()
                        if row is None:
                            raise RuntimeError("superseded")

                    async def drain():
                        for _ in range(300):
                            generation = (
                                await (
                                    await connection.execute(
                                        "SELECT nextval('analytics_export_generation') AS value"
                                    )
                                ).fetchone()
                            )["value"]
                            events = await (
                                await connection.execute(
                                    "SELECT "
                                    "id,source_table,operation,record,before_record,created_at "
                                    "FROM analytics_ingest_events ORDER BY id LIMIT 1000"
                                )
                            ).fetchall()
                            await publisher.ingest(connection, events, generation, fence=fence)
                            if events:
                                async with connection.transaction():
                                    await fence(connection)
                                    await connection.execute(
                                        "DELETE FROM analytics_ingest_events WHERE id=ANY(%s)",
                                        ([item["id"] for item in events],),
                                    )
                            changed = await publisher.once(connection, generation, fence=fence)
                            state = await (
                                await connection.execute(
                                    "SELECT card_bootstrap_complete,"
                                    "EXISTS(SELECT 1 FROM analytics_card_refresh) OR "
                                    "EXISTS(SELECT 1 FROM analytics_card_fanout) OR "
                                    "EXISTS(SELECT 1 FROM analytics_ingest_events) AS pending "
                                    "FROM analytics_pipeline_state WHERE singleton"
                                )
                            ).fetchone()
                            if (
                                state["card_bootstrap_complete"]
                                and not state["pending"]
                                and not changed
                            ):
                                return
                        pytest.fail("Native current-card work did not converge")

                    try:
                        yield publisher, client, connection, fence, drain
                    finally:
                        publisher.close()
        finally:
            await admin.execute(f"DROP DATABASE `{name}` SYNC")


def test_exporter_serves_current_boards_and_live_updates_during_historical_backfill(database):
    now = datetime.now(UTC)
    article_id = uuid.uuid4()
    with database.begin() as session:
        session.add(
            Article(
                id=article_id,
                title="Current article before",
                canonical_url="https://example.invalid/current",
                url_hash="1" * 64,
                discovered_at=now,
                published_to_feed_at=now,
                publication_status="published",
                review_status="approved",
            )
        )
        session.add_all(
            InferenceCall(
                id=uuid.UUID(int=number),
                started_at=now - timedelta(days=30),
                operation="article_analysis",
                request_hash="historical",
                status="succeeded",
                tokens={"inputTokens": 5},
                web_searches=0,
                duration_ms=1,
            )
            for number in range(1, 1001)
        )

    async def verify():
        async with native_publisher() as (_, client, connection, _, _):
            await client.initialize()
            configured = client.settings.model_copy(
                update={"analytics_backfill_batch_size": 1, "cache_enabled": False}
            )
            exporter = AnalyticsExporter(client, configured)
            runtime = AnalyticsRuntime(configured, client=client)
            await connection.execute(
                "UPDATE analytics_pipeline_state SET enabled=false,native_lease_token=NULL,"
                "lease_until=NULL WHERE singleton"
            )

            async def history_is_incomplete():
                state = await (
                    await connection.execute(
                        "SELECT native_bootstrap_complete,card_bootstrap_complete,"
                        "EXISTS(SELECT 1 FROM analytics_bootstrap_cursors "
                        "WHERE source_table='inference_calls' AND NOT complete) AS pending "
                        "FROM analytics_pipeline_state WHERE singleton"
                    )
                ).fetchone()
                assert state["pending"] and not state["native_bootstrap_complete"]
                assert (await client.status())["ready"] == 0
                return state

            async def board():
                return await runtime.article_board(
                    q=None, source_id=None, needs_attention=False, limit=20
                )

            try:
                for _ in range(60):
                    await exporter.once()
                    if (await history_is_incomplete())["card_bootstrap_complete"]:
                        break
                else:
                    pytest.fail("Current-card coverage waited for historical import")

                initial = await board()
                assert initial.counts["published"] == 1
                current = next(item for column in initial.columns for item in column.items)
                assert current.id == article_id and current.title == "Current article before"
                assert initial.snapshot is not None
                assert not exporter.cards.bootstrapping and exporter.bootstrapping
                with pytest.raises(HTTPException) as preparing:
                    await runtime.section("inference", recent_window(30, now))
                assert preparing.value.status_code == 503
                assert preparing.value.detail["code"] == "analytics_preparing"

                with database.begin() as session:
                    session.execute(
                        text("UPDATE articles SET title='Current article after' WHERE id=:id"),
                        {"id": article_id},
                    )
                for _ in range(20):
                    await exporter.once()
                    await history_is_incomplete()
                    updated = await board()
                    current = next(item for column in updated.columns for item in column.items)
                    if current.title == "Current article after":
                        break
                else:
                    pytest.fail("Live current-card invalidations waited for historical import")
                assert updated.counts["published"] == 1
                assert not await (
                    await connection.execute("SELECT 1 FROM analytics_ingest_events LIMIT 1")
                ).fetchone()
                assert await client.query("SELECT * FROM analytics_bucket_manifest FINAL") == []
            finally:
                exporter.close()
                await runtime.close()

    asyncio.run(verify())


def test_job_deadline_changes_schedule_and_cancel_indexed_card_timers(database):
    now = datetime.now(UTC)
    article_id, job_id = uuid.uuid4(), uuid.uuid4()
    with database.begin() as session:
        session.add(
            Article(
                id=article_id,
                title="Scheduled job",
                canonical_url="https://example.invalid/timer",
                url_hash="3" * 64,
                discovered_at=now,
            )
        )

    async def verify():
        async with native_publisher() as (publisher, _, connection, _, drain):
            await drain()
            first, later = now + timedelta(hours=1), now + timedelta(hours=2)
            with database.begin() as session:
                session.add(
                    ArticleEnrichmentJob(
                        id=job_id,
                        article_id=article_id,
                        status="queued",
                        available_at=first,
                    )
                )
            await drain()

            async def scheduled():
                return await (
                    await connection.execute(
                        "SELECT available_at FROM analytics_card_timers "
                        "WHERE kind='article' AND entity_id=%s",
                        (article_id,),
                    )
                ).fetchone()

            assert (await scheduled())["available_at"] == first
            with database.begin() as session:
                session.get(ArticleEnrichmentJob, job_id).available_at = later
            await drain()
            assert (await scheduled())["available_at"] == later
            with database.begin() as session:
                job = session.get(ArticleEnrichmentJob, job_id)
                job.status, job.finished_at = "succeeded", now
            await drain()
            assert await scheduled() is None

    asyncio.run(verify())


def test_refresh_candidates_are_bounded_and_preserve_priority(database):
    engine = database.kw["bind"]
    params = (1600, 1600, 1600, 1600, 8, 200)

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM analytics_card_refresh"))
        connection.execute(
            text("""INSERT INTO analytics_card_refresh
            (kind,entity_id,generation,initial_pending,available_at)
            SELECT 'article',md5(number::text)::uuid,number,false,
                   statement_timestamp()+interval '1 day'
            FROM generate_series(1,100000) AS number""")
        )
        connection.execute(text("ANALYZE analytics_card_refresh"))
        plan = connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + REFRESH_DUE_SQL, params
        ).scalar_one()[0]["Plan"]
        scans = [
            node for node in nodes(plan) if node.get("Relation Name") == "analytics_card_refresh"
        ]
        assert {node.get("Index Name") for node in scans} == {
            "ix_analytics_card_refresh_initial_due",
            "ix_analytics_card_refresh_due",
        }
        assert all("available_at" in node["Index Cond"] for node in scans)
        assert all(node["Actual Rows"] == 0 for node in scans)
        assert plan.get("Shared Hit Blocks", 0) + plan.get("Shared Read Blocks", 0) < 100
        assert connection.exec_driver_sql(REFRESH_DUE_SQL, params).all() == []

        connection.execute(
            text(
                "UPDATE analytics_card_refresh SET "
                "available_at=statement_timestamp()-interval '1 hour', "
                "initial_pending=(generation<=1700)"
            )
        )
        connection.execute(text("ANALYZE analytics_card_refresh"))
        rows = connection.exec_driver_sql(REFRESH_DUE_SQL, params).all()
        # Eight uniformly distributed shards need not contain 200 candidates.
        assert 0 < len(rows) <= 200
        assert all(row.generation <= 1700 for row in rows)
        assert len({row.entity_id.bytes[0] for row in rows}) <= 8
        plan = connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + REFRESH_DUE_SQL, params
        ).scalar_one()[0]["Plan"]
        scans = [
            node for node in nodes(plan) if node.get("Relation Name") == "analytics_card_refresh"
        ]
        assert all(node["Actual Rows"] <= 1600 for node in scans)
        ordinary = next(
            node for node in scans if node.get("Index Name") == "ix_analytics_card_refresh_due"
        )
        assert ordinary["Actual Loops"] == 0


def test_due_timer_index_does_not_scan_a_large_future_population(database):
    engine = database.kw["bind"]
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO analytics_card_timers
                (policy,kind,entity_id,epoch,generation,available_at)
                SELECT 'future-policy','article',md5(number::text)::uuid,0,number,
                       statement_timestamp()+interval '1 day'
                FROM generate_series(1,100000) AS number""")
        )
        connection.execute(text("ANALYZE analytics_card_timers"))
        plan = connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + TIMER_DUE_SQL,
            ("future-policy", 0, 200),
        ).scalar_one()[0]["Plan"]

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    scan = next(
        node for node in nodes(plan) if node.get("Index Name") == "ix_analytics_card_timers_due"
    )
    assert "available_at" in scan["Index Cond"]
    assert "statement_timestamp()" in scan["Index Cond"]
    assert scan["Actual Rows"] == 0 and scan.get("Rows Removed by Filter", 0) == 0
    assert scan.get("Shared Hit Blocks", 0) + scan.get("Shared Read Blocks", 0) < 100


def test_refresh_candidate_window_serves_hot_and_later_shards(database):
    engine = database.kw["bind"]
    params = (1600, 1600, 1600, 1600, 8, 200)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM analytics_card_refresh"))
        connection.execute(
            text("""INSERT INTO analytics_card_refresh
            (kind,entity_id,generation,initial_pending,available_at)
            SELECT 'article',('00'||substr(md5(number::text),3))::uuid,number,false,
                   statement_timestamp()-interval '2 hours'
            FROM generate_series(1,1700) AS number""")
        )
        cold = uuid.UUID("01000000-0000-0000-0000-000000000001")
        connection.execute(
            text("""INSERT INTO analytics_card_refresh
            (kind,entity_id,generation,initial_pending,available_at)
            VALUES ('article',:id,1701,false,statement_timestamp()-interval '1 hour')"""),
            {"id": cold},
        )
        seen = set()
        for _ in range(10):
            rows = connection.exec_driver_sql(REFRESH_DUE_SQL, params).all()
            assert rows
            ids = [row.entity_id for row in rows]
            seen.update(ids)
            connection.execute(
                text("DELETE FROM analytics_card_refresh WHERE entity_id=ANY(:ids)"), {"ids": ids}
            )
            if cold in seen:
                break
        assert cold in seen


def test_existing_card_timer_adoption_retries_fences_and_resets_without_losing_deadlines(database):
    now = datetime.now(UTC)
    first, due, removed, stale = [uuid.UUID(int=value) for value in (1, 2, 3, 4)]

    async def verify():
        async with native_publisher() as (publisher, client, connection, fence, _):
            publisher.settings = publisher.settings.model_copy(
                update={"analytics_backfill_batch_size": 1}
            )
            await publisher._initialize()
            policy = "existing-policy"
            state = {"card_target_policy": policy}
            await connection.execute(
                "UPDATE analytics_pipeline_state SET card_target_policy=%s,"
                "card_serving_policy=%s,card_bootstrap_complete=true WHERE singleton",
                (policy, policy),
            )
            await connection.execute(
                "UPDATE analytics_card_timer_bootstrap SET policy=%s,epoch=0,kind_index=0,"
                "shard=NULL,cursor=NULL,complete=false,cleanup_pending=false WHERE singleton",
                (policy,),
            )
            await client.insert(
                "analytics_card_canonical",
                [
                    normalize_card(
                        dict(
                            entity_id=identifier,
                            created_at=now,
                            activity_at=now,
                            next_transition_at=deadline,
                        ),
                        policy,
                        "article",
                        identifier,
                        5,
                        now,
                    )
                    for identifier, deadline in (
                        (first, now + timedelta(hours=1)),
                        (due, now - timedelta(seconds=1)),
                        (removed, now + timedelta(hours=2)),
                    )
                ],
            )
            # The upgrade bridge must still enqueue an unseeded overdue card.
            assert await publisher._timers_one(connection, 10, state, fence=fence)
            assert await (
                await connection.execute(
                    "SELECT 1 FROM analytics_card_refresh WHERE kind='article' AND entity_id=%s",
                    (due,),
                )
            ).fetchone()
            # A newer cancellation observed while a page is in flight must win
            # over an older ClickHouse canonical deadline from that page.
            async with connection.transaction():
                await fence(connection)
                await publisher._upsert_timers(
                    connection,
                    policy,
                    "article",
                    0,
                    [dict(entity_id=removed, generation=20, available_at=None)],
                )

            original = publisher._upsert_timers

            async def fail_after_write(*args):
                await original(*args)
                raise RuntimeError("connection failed before checkpoint")

            publisher._upsert_timers = fail_after_write
            with pytest.raises(RuntimeError, match="before checkpoint"):
                await publisher._timer_bootstrap_one(connection, 21, state, fence=fence)
            assert not await (
                await connection.execute(
                    "SELECT 1 FROM analytics_card_timers WHERE entity_id=%s", (first,)
                )
            ).fetchone()
            bootstrap = await (
                await connection.execute(
                    "SELECT cursor,complete FROM analytics_card_timer_bootstrap"
                )
            ).fetchone()
            assert bootstrap == {"cursor": None, "complete": False}
            publisher._upsert_timers = original
            restarted = CardPublisher(client, publisher.settings)
            try:
                for generation in range(22, 1000):
                    await restarted._timer_bootstrap_one(connection, generation, state, fence=fence)
                    complete = await (
                        await connection.execute(
                            "SELECT complete FROM analytics_card_timer_bootstrap"
                        )
                    ).fetchone()
                    if complete["complete"]:
                        break
                else:
                    pytest.fail("Timer adoption did not finish")
                timers = await (
                    await connection.execute(
                        "SELECT entity_id,generation,available_at FROM analytics_card_timers "
                        "WHERE policy=%s",
                        (policy,),
                    )
                ).fetchall()
                timers = {row["entity_id"]: row for row in timers}
                assert timers[first]["available_at"] == now + timedelta(hours=1)
                assert timers[due]["generation"] == 10 and timers[due]["available_at"] is None
                assert (
                    timers[removed]["generation"] == 20 and timers[removed]["available_at"] is None
                )
                await connection.execute(
                    "UPDATE analytics_card_timers SET "
                    "available_at=clock_timestamp()-interval '1 second' "
                    "WHERE entity_id=%s",
                    (first,),
                )

                async def lost(_connection):
                    raise RuntimeError("lease superseded")

                with pytest.raises(RuntimeError, match="lease superseded"):
                    await restarted._timers_one(connection, 50, state, fence=lost)
                assert await (
                    await connection.execute(
                        "SELECT 1 FROM analytics_card_timers WHERE entity_id=%s", (first,)
                    )
                ).fetchone()
                original_query = client.query

                async def no_scan(*_args, **_kwargs):
                    pytest.fail("Completed timer adoption scanned the ClickHouse population")

                client.query = no_scan
                try:
                    assert await restarted._timers_one(connection, 51, state, fence=fence)
                    assert not await (
                        await connection.execute(
                            "SELECT 1 FROM analytics_card_timers WHERE entity_id=%s", (first,)
                        )
                    ).fetchone()
                    assert await (
                        await connection.execute(
                            "SELECT 1 FROM analytics_card_refresh WHERE entity_id=%s", (first,)
                        )
                    ).fetchone()
                finally:
                    client.query = original_query

                await restarted._upsert_timers(
                    connection,
                    policy,
                    "article",
                    0,
                    [dict(entity_id=stale, generation=40, available_at=now - timedelta(seconds=1))],
                )
                await restarted._reset(connection, policy, 100, fence=fence)
                await restarted._upsert_timers(
                    connection,
                    policy,
                    "article",
                    100,
                    [dict(entity_id=first, generation=101, available_at=now + timedelta(hours=4))],
                )
                assert not await restarted._timers_one(connection, 101, state, fence=fence)
                for generation in range(102, 110):
                    await restarted._timer_bootstrap_one(connection, generation, state, fence=fence)
                    cleaned = await (
                        await connection.execute(
                            "SELECT cleanup_pending FROM analytics_card_timer_bootstrap"
                        )
                    ).fetchone()
                    if not cleaned["cleanup_pending"]:
                        break
                assert not cleaned["cleanup_pending"]
                surviving = await (
                    await connection.execute(
                        "SELECT entity_id,epoch,available_at FROM analytics_card_timers"
                    )
                ).fetchall()
                assert surviving == [
                    dict(entity_id=first, epoch=100, available_at=now + timedelta(hours=4))
                ]
            finally:
                restarted.close()

    asyncio.run(verify())


def test_native_current_cards_bootstrap_fanout_noop_delete_and_reset(database):
    now = datetime.now(UTC)
    with database() as session:
        source = Source(
            name="Publisher before",
            source_type="publisher",
            website_url="https://example.invalid/private-token",
            feed_url="https://example.invalid/private-feed?key=private-key",
            approval_status="approved",
            enabled=True,
        )
        session.add(source)
        session.flush()
        source_id = source.id
        articles = [
            Article(
                id=uuid.UUID(int=(0x12 << 120) + number),
                title=f"Historical article {number}",
                canonical_url=f"https://example.invalid/{number}",
                url_hash=f"{number:064x}",
                discovered_at=now - timedelta(days=40),
                published_to_feed_at=now - timedelta(days=30),
                publication_status="published",
                review_status="approved",
                origins=[
                    ArticleOrigin(
                        source_id=source_id,
                        entry_key=str(number),
                        original_url=f"https://example.invalid/{number}",
                    )
                ],
            )
            for number in range(1, 1006)
        ]
        session.add_all(articles)
        session.commit()
    entity = articles[0].id

    async def verify():
        async with native_publisher() as (publisher, client, connection, fence, drain):
            await drain()
            status = (await client.query("SELECT * FROM analytics_card_status FINAL"))[0]
            assert status["ready"] == 1 and not publisher.bootstrapping
            assert (
                await client.query(
                    "SELECT sum(item_count) AS n FROM analytics_card_manifest FINAL "
                    "WHERE kind='article'"
                )
            )[0]["n"] == 1005
            assert (
                await client.query(
                    "SELECT count() AS n FROM analytics_card_canonical FINAL WHERE "
                    "kind='article' AND review_pending=0"
                )
            )[0]["n"] == 1005
            source_payload = json.loads(
                (
                    await client.query(
                        "SELECT payload FROM analytics_card_canonical FINAL WHERE kind='source'"
                    )
                )[0]["payload"]
            )
            assert source_payload["website_url"] is None and source_payload["feed_url"] is None

            before = (
                await client.query(
                    "SELECT generation FROM analytics_card_manifest FINAL WHERE "
                    "kind='article' AND shard=18"
                )
            )[0]["generation"]
            with database() as session:
                session.execute(
                    text("UPDATE sources SET name='Publisher after' WHERE id=:id"),
                    {"id": source_id},
                )
                session.commit()
            await drain()
            assert (
                await client.query(
                    "SELECT count() AS n FROM analytics_card_canonical FINAL WHERE "
                    "kind='article' AND source_name='Publisher after'"
                )
            )[0]["n"] == 1005
            renamed = (
                await client.query(
                    "SELECT generation FROM analytics_card_manifest FINAL WHERE "
                    "kind='article' AND shard=18"
                )
            )[0]["generation"]
            assert renamed > before

            async with connection.transaction():
                await publisher._mark(connection, {("article", entity)}, 999999, False)
            await drain()
            assert (
                await client.query(
                    "SELECT generation FROM analytics_card_manifest FINAL WHERE "
                    "kind='article' AND shard=18"
                )
            )[0]["generation"] == renamed

            with database() as session:
                session.execute(text("DELETE FROM articles WHERE id=:id"), {"id": entity})
                session.commit()
            await drain()
            assert (
                await client.query(
                    "SELECT deleted FROM analytics_card_canonical FINAL WHERE "
                    "kind='article' AND entity_id={id:UUID}",
                    {"id": entity},
                )
            )[0]["deleted"] == 1
            assert (
                await client.query(
                    "SELECT item_count FROM analytics_card_manifest FINAL WHERE "
                    "kind='article' AND shard=18"
                )
            )[0]["item_count"] == 1004

            await client.execute(
                "CREATE TABLE analytics_historical_sentinel(value UInt64) "
                "ENGINE=MergeTree ORDER BY value"
            )
            await client.insert("analytics_historical_sentinel", [{"value": 42}])
            await connection.execute(
                "UPDATE analytics_pipeline_state SET native_bootstrap_complete=true"
            )
            with database() as session:
                session.execute(text("TRUNCATE articles CASCADE"))
                session.commit()
            await drain()
            assert (
                await client.query(
                    "SELECT sum(item_count) AS n FROM analytics_card_manifest FINAL "
                    "WHERE kind='article'"
                )
            )[0]["n"] == 0
            assert (
                await client.query(
                    "SELECT sum(item_count) AS n FROM analytics_card_manifest FINAL "
                    "WHERE kind='source'"
                )
            )[0]["n"] == 1
            assert (await client.query("SELECT value FROM analytics_historical_sentinel"))[0][
                "value"
            ] == 42
            assert (
                await (
                    await connection.execute(
                        "SELECT native_bootstrap_complete FROM analytics_pipeline_state"
                    )
                ).fetchone()
            )["native_bootstrap_complete"]

    asyncio.run(verify())


def test_native_card_batch_bounds_shards_and_keeps_racing_generations(database):
    now = datetime.now(UTC)
    identifiers = [
        uuid.UUID(int=(shard << 120) + number) for shard in range(16, 25) for number in (1, 2)
    ]
    with database() as session:
        session.add_all(
            Article(
                id=value,
                title="Before batch",
                canonical_url=f"https://example.invalid/{value}",
                url_hash=f"{index:064x}",
                discovered_at=now,
            )
            for index, value in enumerate(identifiers)
        )
        session.commit()

    async def verify():
        async with native_publisher() as (publisher, client, connection, fence, drain):
            await drain()
            generation = (
                await (
                    await connection.execute(
                        "SELECT nextval('analytics_export_generation') AS value"
                    )
                ).fetchone()
            )["value"]
            with database() as session:
                session.execute(text("UPDATE articles SET title='Queued batch'"))
                session.commit()
            await publisher._mark(
                connection, {("article", value) for value in identifiers}, generation, False
            )
            # The highest UUID shard is oldest and must make progress even when
            # lower UUID shards can fill the bounded builder batch.
            raced = identifiers[-1]
            await connection.execute(
                "UPDATE analytics_card_refresh SET "
                "available_at=clock_timestamp()-interval '1 hour' "
                "WHERE entity_id=%s",
                (raced,),
            )
            original = publisher._build
            built = []
            dsn = settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)

            def build(kind, values, policy, observed):
                built.append(list(values))
                result = original(kind, values, policy, observed)
                if raced in values:
                    with psycopg.connect(dsn) as writer:
                        writer.execute(
                            "UPDATE articles SET title='Racing batch' WHERE id=%s", (raced,)
                        )
                        writer.execute(
                            "UPDATE analytics_card_refresh SET generation=%s WHERE entity_id=%s",
                            (generation + 2, raced),
                        )
                return result

            publisher._build = build
            state = await publisher._state(connection)
            assert await publisher._refresh_one(connection, generation + 1, state, fence=fence)
            assert raced in built[0]
            assert len(built[0]) == 16
            assert len({value.bytes[0] for value in built[0]}) == 8
            pending = await (
                await connection.execute("SELECT entity_id,generation FROM analytics_card_refresh")
            ).fetchall()
            assert len(pending) == 3
            assert {row["entity_id"] for row in pending} == {
                value for value in identifiers if value not in built[0]
            } | {raced}
            assert next(row["generation"] for row in pending if row["entity_id"] == raced) == (
                generation + 2
            )
            manifests = await client.query(
                "SELECT shard,item_count FROM analytics_card_manifest FINAL "
                "WHERE kind='article' AND generation={generation:UInt64}",
                {"generation": generation + 1},
            )
            assert len(manifests) == 8 and sum(row["item_count"] for row in manifests) == 16
            publisher._build = original
            assert await publisher._refresh_one(connection, generation + 3, state, fence=fence)
            assert not await (
                await connection.execute("SELECT 1 FROM analytics_card_refresh")
            ).fetchall()
            assert (
                await client.query(
                    "SELECT title FROM analytics_card_canonical FINAL WHERE entity_id={id:UUID}",
                    {"id": raced},
                )
            )[0]["title"] == "Racing batch"

    asyncio.run(verify())
