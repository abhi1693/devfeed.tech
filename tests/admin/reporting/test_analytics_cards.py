"""Native PostgreSQL delivery and ClickHouse current-card lifecycle regressions."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_cards import CardPublisher
from devfeed_core.models import Article, ArticleOrigin, Source
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
                        "UPDATE analytics_pipeline_state SET enabled=true,lease_token=%s,"
                        "lease_until=clock_timestamp()+interval '1 hour' WHERE singleton",
                        (token,),
                    )

                    async def fence(connection):
                        row = await (
                            await connection.execute(
                                "SELECT 1 FROM analytics_pipeline_state WHERE singleton "
                                "AND lease_token=%s "
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
