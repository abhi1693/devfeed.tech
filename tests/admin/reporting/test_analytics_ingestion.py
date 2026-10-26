"""Native analytical facts remain exact through capture, replay, and source outages."""

import asyncio
import json
import os
import socket
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from clickhouse_connect.driver.exceptions import Error as ClickHouseError
from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable
from devfeed_core.analytics_facts import PAYLOAD_NAMES, empty_fact
from devfeed_core.analytics_rollups import (
    REFRESH_BATCH_SIZE,
    RollupPublisher,
    canonical_sql,
    rebuild_sql,
)
from devfeed_core.analytics_schema import BACKFILL_TABLES
from devfeed_core.analytics_worker import AnalyticsExporter, postgres_dsn
from devfeed_core.config import Settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleOpen,
    InferenceCall,
    OverviewDaily,
    TopicDecisionRun,
    TopicProposal,
    UserAccount,
    utcnow,
)
from devfeed_core.version import SCHEMA_REVISION
from psycopg.rows import dict_row
from sqlalchemy import text

from tests.support import legacy_migrations
from tests.support.paths import REPO_ROOT

pre_native_database = legacy_migrations.pre_native_database


def test_official_driver_bindings_budgets_and_durable_async_ack():
    async def verify():
        settings = Settings(
            _env_file=None, database_url="postgresql://unused", redis_url="redis://unused"
        )
        driver = SimpleNamespace(
            raw_query=AsyncMock(
                side_effect=[b'{"value":1}\n', b'{"name":"event_id","type":"UInt64"}\n']
            ),
            insert=AsyncMock(),
            close=AsyncMock(),
        )
        async with AnalyticsClient(settings, driver=driver) as client:
            assert await client.query("SELECT {value:String}", {"value": "'unsafe SQL;"}) == [
                {"value": 1}
            ]
            await client.insert("analytics_fact_events", [{"event_id": 1}])
        assert driver.raw_query.call_args_list[0].kwargs["parameters"] == {"value": "'unsafe SQL;"}
        limits = driver.insert.call_args.kwargs["settings"]
        assert limits["max_threads"] == 2
        assert limits["async_insert"] == limits["wait_for_async_insert"] == 1
        assert limits["insert_deduplicate"] == limits["async_insert_deduplicate"] == 0
        driver.close.assert_awaited_once()

    asyncio.run(verify())


def test_native_insert_caches_types_and_keeps_concurrent_batches_independent():
    async def verify():
        settings = Settings(
            _env_file=None, database_url="postgresql://unused", redis_url="redis://unused"
        )
        columns = {
            "id": "Nullable(UUID)",
            "ids": "Array(UUID)",
            "at": "Nullable(DateTime64(6,'UTC'))",
            "day": "Date",
            "version": "UInt128",
            "usage": "Int128",
        }
        schema = "\n".join(
            json.dumps({"name": name, "type": kind}) for name, kind in columns.items()
        )
        describing, release_schema = asyncio.Event(), asyncio.Event()

        async def describe(*_args, **_kwargs):
            describing.set()
            await release_schema.wait()
            return schema.encode()

        driver = SimpleNamespace(
            raw_query=AsyncMock(side_effect=describe),
            insert=AsyncMock(),
            close=AsyncMock(),
        )
        identifier = uuid.uuid4()
        instant = datetime(2026, 10, 27, 1, 2, 3, 456789, tzinfo=UTC)
        first = dict(
            id=None,
            ids=[identifier, str(identifier)],
            at=instant.isoformat(),
            day="2026-10-27",
            version=2**100 + 19,
            usage=-(2**90),
        )
        second = dict(first, id=identifier, at=instant.astimezone(timezone(timedelta(hours=5))))
        async with AnalyticsClient(settings, driver=driver) as client:
            first_insert = asyncio.create_task(
                client.insert("analytics_type_probe", [first, second])
            )
            await describing.wait()
            second_insert = asyncio.create_task(client.insert("analytics_type_probe", [second]))
            await asyncio.sleep(0)
            release_schema.set()
            await asyncio.gather(first_insert, second_insert)
            await client.insert("analytics_type_probe", [first])
        driver.raw_query.assert_awaited_once()
        calls = [call.kwargs for call in driver.insert.call_args_list]
        assert len(calls) == 3
        assert len({id(call["data"]) for call in calls}) == 3
        assert all("context" not in call for call in calls)
        for call, originals in zip(calls, ([first, second], [second], [first]), strict=True):
            assert call["column_type_names"] == tuple(columns[name] for name in sorted(columns))
            assert call["column_oriented"] is True
            decoded = [
                dict(zip(call["column_names"], row, strict=True))
                for row in zip(*call["data"], strict=True)
            ]
            assert decoded == [
                dict(
                    original,
                    id=str(identifier) if original["id"] else None,
                    ids=[str(identifier), str(identifier)],
                    at=instant,
                    day=date(2026, 10, 27),
                )
                for original in originals
            ]

    asyncio.run(verify())


def test_query_bindings_preserve_nested_uuid_arrays_dates_and_scalar_types():
    async def verify():
        settings = Settings(
            _env_file=None, database_url="postgresql://unused", redis_url="redis://unused"
        )
        driver = SimpleNamespace(raw_query=AsyncMock(return_value=b'{"ok":1}'), close=AsyncMock())
        identifier = uuid.uuid4()
        at = datetime(2026, 10, 27, 1, 2, 3, 456789, tzinfo=timezone(timedelta(hours=5)))
        sql = (
            "SELECT {ids:Map(String, Array(UUID))}, {day:Date}, "
            "{at:DateTime64(6, 'UTC')}, {enabled:Bool}, {cursor:UInt64}, "
            "{optional:Nullable(String)}"
        )
        async with AnalyticsClient(settings, driver=driver) as client:
            result = await client.query(
                sql,
                dict(
                    ids={"selected": (identifier,), "empty": []},
                    day=date(2026, 10, 27),
                    at=at,
                    enabled=True,
                    cursor=17,
                    optional=None,
                ),
            )
        assert result == [{"ok": 1}]
        call = driver.raw_query.call_args
        assert call.args == (sql,)
        assert call.kwargs["parameters"] == {
            "ids": {"selected": [str(identifier)], "empty": []},
            "day": "2026-10-27",
            "at": "2026-10-26 20:02:03.456789",
            "enabled": True,
            "cursor": 17,
            "optional": None,
        }

    asyncio.run(verify())


def test_native_batches_normalize_nullable_and_calendar_values_without_rewriting_epoch_values():
    async def verify():
        settings = Settings(
            _env_file=None, database_url="postgresql://unused", redis_url="redis://unused"
        )
        kinds = {
            "at": "Nullable(DateTime64(6, 'UTC'))",
            "epoch": "DateTime",
            "day": "Date",
            "nullable_day": "Nullable(Date32)",
            "labels": "Array(String)",
            "name": "LowCardinality(Nullable(String))",
        }
        schema = "\n".join(json.dumps({"name": name, "type": kind}) for name, kind in kinds.items())
        driver = SimpleNamespace(
            raw_query=AsyncMock(return_value=schema.encode()),
            insert=AsyncMock(),
            close=AsyncMock(),
        )
        aware = datetime(2026, 10, 27, 1, tzinfo=timezone(timedelta(hours=5)))
        naive = datetime(2026, 10, 27, 2, 3, 4, 567890)
        rows = [
            dict(at=None, epoch=0, day=aware, nullable_day=None, labels=["first"], name=None),
            dict(
                at=aware,
                epoch=1,
                day=naive,
                nullable_day=date(2026, 10, 27),
                labels=[],
                name="€",
            ),
            dict(
                at=naive,
                epoch=2,
                day=date(2026, 10, 28),
                nullable_day=date(2026, 10, 28),
                labels=["last"],
                name="plain",
            ),
        ]
        async with AnalyticsClient(settings, driver=driver) as client:
            await client.insert("analytics_calendar_probe", rows)
        call = driver.insert.call_args.kwargs
        decoded = [
            dict(zip(call["column_names"], row, strict=True))
            for row in zip(*call["data"], strict=True)
        ]
        assert decoded == [
            dict(rows[0], day=date(2026, 10, 26)),
            dict(rows[1], at=aware.astimezone(UTC), day=date(2026, 10, 27)),
            dict(rows[2], at=naive.replace(tzinfo=UTC)),
        ]
        assert call["column_oriented"] is True
        assert call["settings"]["wait_for_async_insert"] == 1

    asyncio.run(verify())


def test_native_insert_rejects_mixed_columns_without_silently_filling_defaults():
    async def verify():
        driver = SimpleNamespace(close=AsyncMock())
        settings = Settings(
            _env_file=None, database_url="postgresql://unused", redis_url="redis://unused"
        )
        async with AnalyticsClient(settings, driver=driver) as client:
            with pytest.raises(ValueError, match="same columns"):
                await client.insert("analytics_fact_events", [{"event_id": 1}, {"side": 0}])
            with pytest.raises(ValueError, match="Unknown analytical table"):
                await client.insert("untrusted; SQL", [{"event_id": 1}])

    asyncio.run(verify())


def test_native_insert_includes_schema_discovery_in_total_deadline():
    async def verify():
        async def describe(*_args, **_kwargs):
            await asyncio.Event().wait()

        driver = SimpleNamespace(raw_query=AsyncMock(side_effect=describe), close=AsyncMock())
        settings = Settings(
            _env_file=None,
            database_url="postgresql://unused",
            redis_url="redis://unused",
            clickhouse_timeout_seconds=0.01,
        )
        async with AnalyticsClient(settings, driver=driver) as client:
            with pytest.raises(AnalyticsUnavailable, match="ClickHouse request failed"):
                await client.insert("analytics_fact_events", [{"event_id": 1}])

    asyncio.run(verify())


def test_unavailable_analytics_does_not_expose_server_errors_or_credentials():
    async def verify():
        settings = Settings(
            _env_file=None, database_url="postgresql://unused", redis_url="redis://unused"
        )
        driver = SimpleNamespace(
            raw_query=AsyncMock(side_effect=ClickHouseError("private details")), close=AsyncMock()
        )
        async with AnalyticsClient(settings, driver=driver) as client:
            with pytest.raises(AnalyticsUnavailable, match="ClickHouse request failed"):
                await client.query("SELECT 1")

    asyncio.run(verify())


@pytest.mark.integration
def test_native_downgrade_fails_before_altering_pending_facts_or_live_queue_indexes(
    pre_native_database,
):
    database = pre_native_database
    config = Config(str(REPO_ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    identifier = uuid.uuid4()
    with database.begin() as session:
        session.execute(
            text(
                "UPDATE analytics_pipeline_state SET enabled=true, "
                "capture_started_at=clock_timestamp()"
            )
        )
        session.add(_call(identifier, utcnow()))
    with database() as session:
        pending = list(session.scalars(text("SELECT id FROM analytics_ingest_events ORDER BY id")))
        indexes = session.execute(
            text(
                "SELECT oid,relname FROM pg_class "
                "WHERE relname LIKE 'ix_%_analytics_active' ORDER BY relname"
            )
        ).all()
        assert pending and len(indexes) == 8
    with pytest.raises(RuntimeError, match="backup"):
        command.downgrade(config, "0038")
    with database() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
        assert session.get(InferenceCall, identifier) is not None
        assert (
            list(session.scalars(text("SELECT id FROM analytics_ingest_events ORDER BY id")))
            == pending
        )
        assert (
            session.execute(
                text(
                    "SELECT oid,relname FROM pg_class "
                    "WHERE relname LIKE 'ix_%_analytics_active' ORDER BY relname"
                )
            ).all()
            == indexes
        )


@pytest.mark.integration
def test_pending_v1_updates_cross_native_cutover_without_old_cohort_or_attempt_duplicates(
    pre_native_database,
):
    database = pre_native_database
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    first_hour = now - timedelta(hours=2)
    call_id, article_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with database.begin() as session:
        session.execute(
            text(
                "UPDATE analytics_export_state SET enabled=true, "
                "capture_started_at=clock_timestamp()"
            )
        )
        session.add(
            Article(
                id=article_id,
                canonical_url="https://pending.example/article",
                url_hash=uuid.uuid4().hex,
                title="Pending capture",
            )
        )
        session.add(_call(call_id, first_hour))
        session.flush()
        session.add(
            ArticleAnalysisJob(
                id=job_id,
                article_id=article_id,
                input_hash="pending",
                input_snapshot={},
                editorial_revision=0,
                status="failed",
                attempts=1,
                finished_at=first_hour,
                duration_ms=12,
            )
        )
    with database.begin() as session:
        call = session.get(InferenceCall, call_id)
        call.started_at, call.finished_at, call.status = now, now, "returned"
        call.tokens = {"inputTokens": 10, "outputTokens": 3}
        job = session.get(ArticleAnalysisJob, job_id)
        job.status, job.finished_at = "queued", None
    with database.begin() as session:
        job = session.get(ArticleAnalysisJob, job_id)
        job.status, job.attempts, job.finished_at, job.outcome = "succeeded", 2, now, "applied"
    with database() as session:
        legacy_ids = set(session.scalars(text("SELECT id FROM analytics_outbox")))
        assert len(legacy_ids) >= 6
    command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    with database() as session:
        assert set(session.scalars(text("SELECT format_version FROM analytics_ingest_events"))) == {
            1
        }
    with database.begin() as session:
        session.get(InferenceCall, call_id).model = "native update"
    with database() as session:
        assert set(session.scalars(text("SELECT format_version FROM analytics_ingest_events"))) == {
            1,
            3,
        }

    async def verify():
        settings = _settings().model_copy(
            update={
                "database_url": database.kw["bind"].url.render_as_string(hide_password=False),
                "clickhouse_database": f"native_pending_{uuid.uuid4().hex}_test",
            }
        )
        async with AnalyticsClient(
            settings.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"CREATE DATABASE `{settings.clickhouse_database}`")
            try:
                async with AnalyticsClient(settings) as client:
                    await _reset(client)
                    await _drain_initial(AnalyticsExporter(client, settings), client)
                    calls = await _facts(client, "inference", now)
                    assert len(calls) == 1 and calls[0]["input_tokens"] == 10
                    assert calls[0]["model"] == "native update"
                    assert calls[0]["bucket_hour"].startswith(now.strftime("%Y-%m-%dT%H:"))
                    attempts = await _facts(client, "processing", now)
                    completed = [row for row in attempts if row["completed"]]
                    assert len(completed) == 1 and completed[0]["attempt"] == 2
                    assert len(attempts) == 2 and sum(row["failed"] for row in attempts) == 1
                    audit = await client.query(
                        "SELECT event_id,record FROM analytics_v1_pending_archive FINAL"
                    )
                    assert {row["event_id"] for row in audit} == legacy_ids
                    assert any(json.loads(row["record"]).get("status") == "queued" for row in audit)
                with database() as session:
                    assert session.scalar(text("SELECT count(*) FROM analytics_ingest_events")) == 0
            finally:
                await admin.execute(f"DROP DATABASE `{settings.clickhouse_database}`")

    asyncio.run(verify())


def _settings():
    url = os.environ.get("DEVFEED_TEST_CLICKHOUSE_URL")
    if not url:
        pytest.skip("Set DEVFEED_TEST_CLICKHOUSE_URL for native ClickHouse integration tests")
    database = os.environ.get("DEVFEED_TEST_CLICKHOUSE_DATABASE", "devfeed_test")
    if not database.endswith("_test"):
        pytest.fail("Disposable ClickHouse database name must end with _test")
    return Settings(
        _env_file=None,
        database_url=os.environ["DEVFEED_TEST_DATABASE_URL"],
        redis_url=os.environ["DEVFEED_TEST_REDIS_URL"],
        clickhouse_url=url,
        clickhouse_database=database,
        clickhouse_username=os.environ.get("DEVFEED_TEST_CLICKHOUSE_USERNAME", "default"),
        clickhouse_password=os.environ.get("DEVFEED_TEST_CLICKHOUSE_PASSWORD", ""),
        analytics_rollup_interval_seconds=1,
    )


@pytest.mark.integration
def test_native_compressed_batches_preserve_capture_types_and_omitted_server_defaults():
    settings = _settings()

    async def verify():
        target = f"native_types_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(
            settings.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": target})
                ) as client:
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    await client.execute(
                        "CREATE TABLE analytics_type_probe (position UInt8,identity Nullable(UUID),"
                        "identities Array(UUID),at Nullable(DateTime64(6,'UTC')),day Date,"
                        "version UInt128,usage Int128,label String,server_default UInt8 DEFAULT 7) "
                        "ENGINE=MergeTree ORDER BY position"
                    )
                    identifier = uuid.uuid4()
                    instant = datetime(2026, 10, 27, 1, 2, 3, 456789, tzinfo=UTC)
                    batch = [
                        dict(
                            position=0,
                            identity=None,
                            identities=[identifier, str(identifier)],
                            at=None,
                            day="2026-10-27",
                            version=2**100 + 19,
                            usage=-(2**90),
                            label="Unicode: λ — native",
                        ),
                        dict(
                            position=1,
                            identity=identifier,
                            identities=[],
                            at=instant.isoformat(),
                            day=instant,
                            version=2**100 + 20,
                            usage=2**90,
                            label="",
                        ),
                    ]
                    await client.insert("analytics_type_probe", batch)
                    rows = await client.query(
                        "SELECT * FROM analytics_type_probe ORDER BY position"
                    )
                    assert len(rows) == 2
                    assert rows[0]["identity"] is rows[0]["at"] is None
                    assert rows[0]["identities"] == [str(identifier), str(identifier)]
                    assert rows[1]["identity"] == str(identifier) and rows[1]["identities"] == []
                    assert datetime.fromisoformat(rows[1]["at"]) == instant
                    for index, row in enumerate(rows):
                        assert row["day"] == "2026-10-27"
                        assert row["version"] == batch[index]["version"]
                        assert row["usage"] == batch[index]["usage"]
                        assert row["label"] == batch[index]["label"]
                        assert row["server_default"] == 7
            finally:
                await admin.execute(f"DROP DATABASE `{target}`")

    asyncio.run(verify())


async def _reset(client):
    await client.initialize()
    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
    tables = await client.query(
        "SELECT name FROM system.tables WHERE database=currentDatabase() "
        "AND engine IN ('MergeTree','ReplacingMergeTree','AggregatingMergeTree') "
        "AND startsWith(name,'analytics_') AND NOT startsWith(name,'analytics_v1_archive_')"
    )
    for table in tables:
        await client.execute(f"TRUNCATE TABLE `{table['name']}`")


async def _drain_initial(exporter, client):
    for _ in range(len(BACKFILL_TABLES) + 40):
        await exporter.once()
        status = await client.status()
        if status["ready"]:
            return
    pytest.fail("Native bootstrap never published complete initial coverage")


async def _facts(client, domain, now):
    return await client.query(
        canonical_sql(domain),
        {
            "bucket": now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=3),
            "end": now + timedelta(days=1),
        },
    )


def _call(identifier, now):
    return InferenceCall(
        id=identifier,
        started_at=now,
        operation="article_analysis",
        request_hash="private-hash",
        status="running",
        tokens={"inputTokens": 5, "private_payload": "must stay private"},
        web_searches=0,
        duration_ms=0,
    )


@pytest.mark.integration
def test_first_clickhouse_bootstrap_preserves_closed_postgres_aggregate_only_history(database):
    from devfeed_admin_api.analytics_reports import Reports
    from devfeed_admin_api.overview_windows import recent_window

    settings = _settings()
    now = utcnow()
    day = now.date() - timedelta(days=45)
    with database.begin() as session:
        session.add(
            OverviewDaily(
                day=day,
                metrics={"opens": 42, "readers": None, "accounts": 7, "published": 11},
                updated_at=now,
            )
        )

    async def verify():
        async with AnalyticsClient(settings) as client:
            await _reset(client)
            exporter = AnalyticsExporter(client, settings)
            await _drain_initial(exporter, client)
            status = await client.status()
            capture = datetime.fromisoformat(status["capture_started_at"].replace("Z", "+00:00"))
            audience = await Reports(
                client, recent_window(60, now), now, capture_started_at=capture
            ).audience()
            point = next(point for point in audience.activity if point.bucket.startswith(str(day)))
            assert point.opens == 42 and point.estimated_readers is None
            assert point.new_accounts == 7
            archived = await client.query("SELECT * FROM analytics_audience_archive_daily FINAL")
            assert len(archived) == 1 and archived[0]["published"] == 11
            assert archived[0]["version"] > 0
            assert await _facts(client, "audience", now) == []

    asyncio.run(verify())


@pytest.mark.integration
def test_native_replay_late_commits_and_before_images_move_exact_fact_cohorts(database):
    settings = _settings()
    now = utcnow()
    first, late, later = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with database.begin() as session:
        session.add(_call(first, now - timedelta(days=1)))

    async def verify():
        async with AnalyticsClient(settings) as client:
            await _reset(client)
            exporter = AnalyticsExporter(client, settings)
            await _drain_initial(exporter, client)
            assert len(await _facts(client, "inference", now)) == 1
            with psycopg.connect(postgres_dsn(settings)) as delayed:
                delayed.execute(
                    "INSERT INTO inference_calls(id,started_at,operation,request_hash,"
                    "status,tokens,web_searches,duration_ms) "
                    "VALUES(%s,%s,'article_analysis','private','running','{}',0,0)",
                    (late, now),
                )
                with database.begin() as session:
                    session.add(_call(later, now))
                await exporter.once()
                delayed.commit()
            await exporter.once()
            assert len(await _facts(client, "inference", now)) == 3
            with database.begin() as session:
                session.get(InferenceCall, later).tokens = {
                    "inputTokens": {"private": "must never enter capture"},
                    "outputTokens": "7" * 10000,
                }
            with database() as session:
                captured = session.scalar(
                    text(
                        "SELECT record FROM analytics_ingest_events "
                        "WHERE source_table='inference_calls' AND record->>'id'=:id"
                    ),
                    {"id": str(later)},
                )
                assert captured["tokens"]["inputTokens"] is None
                assert captured["tokens"]["outputTokens"] == "7" * 19
            await exporter.once()
            invalid = [
                row
                for row in await _facts(client, "inference", now)
                if row["entity_id"] == str(later)
            ][0]
            assert invalid["input_tokens"] == invalid["output_tokens"] == 0
            with database.begin() as session:
                row = session.get(InferenceCall, first)
                row.status, row.started_at, row.finished_at = "returned", now, now
                row.tokens = {"inputTokens": 10, "outputTokens": 3}

            class LostAcknowledgement:
                async def insert(self, table, rows):
                    await client.insert(table, rows)
                    if rows:
                        raise RuntimeError("lost after durable fact insert")

            exporter.client = LostAcknowledgement()
            with pytest.raises(RuntimeError, match="lost after durable"):
                await exporter.once()
            with database() as session:
                assert session.scalar(text("SELECT count(*) FROM analytics_ingest_events")) > 0
            exporter.client = client
            await exporter.once()
            rows = await _facts(client, "inference", now)
            assert len(rows) == 3  # old hour tombstone cancels before new cohort appears
            completed = [row for row in rows if row["status"] == "returned"]
            assert len(completed) == 1 and completed[0]["input_tokens"] == 10
            assert completed[0]["output_tokens"] == 3
            with database() as session:
                assert session.scalar(text("SELECT count(*) FROM analytics_ingest_events")) == 0
            for _ in range(3):
                await exporter.once()
            assert len(await _facts(client, "inference", now)) == 3
            names = {
                row["name"] for row in await client.query("DESCRIBE TABLE analytics_fact_events")
            }
            assert not names.intersection({"tokens", "payload", "email", "request_hash", "profile"})

    asyncio.run(verify())


@pytest.mark.integration
def test_retention_keeps_receipts_explicit_delete_cancels_and_retry_keeps_failed_attempt(database):
    settings = _settings()
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    article_id, job_id = uuid.uuid4(), uuid.uuid4()
    with database.begin() as session:
        session.add(
            Article(
                id=article_id,
                canonical_url="https://example.test/analytics",
                url_hash=uuid.uuid4().hex,
                title="Analytics",
            )
        )
        session.flush()
        session.add(
            ArticleAnalysisJob(
                id=job_id,
                article_id=article_id,
                input_hash="analytics",
                input_snapshot={},
                editorial_revision=0,
                status="failed",
                attempts=1,
                finished_at=now,
                duration_ms=12,
            )
        )

    async def verify():
        async with AnalyticsClient(settings) as client:
            await _reset(client)
            exporter = AnalyticsExporter(client, settings)
            await _drain_initial(exporter, client)
            with database.begin() as session:
                session.add(
                    ArticleOpen(article_id=article_id, viewer_key="anonymous", opened_hour=now)
                )
            await exporter.once()
            with database.begin() as session:
                session.execute(text("SET LOCAL devfeed.analytics_retention_delete='on'"))
                session.execute(text("DELETE FROM article_opens"))
                job = session.get(ArticleAnalysisJob, job_id)
                job.status, job.finished_at = "queued", None
            await exporter.once()
            assert sum(row["opens"] for row in await _facts(client, "audience", now)) == 1
            assert sum(row["failed"] for row in await _facts(client, "processing", now)) == 1
            with database.begin() as session:
                session.add(
                    ArticleOpen(article_id=article_id, viewer_key="anonymous", opened_hour=now)
                )
                job = session.get(ArticleAnalysisJob, job_id)
                job.status, job.attempts, job.finished_at = (
                    "succeeded",
                    2,
                    now + timedelta(minutes=1),
                )
                job.outcome = "applied"
            await exporter.once()
            outcomes = await _facts(client, "processing", now)
            assert len(outcomes) == 2 and sum(row["failed"] for row in outcomes) == 1
            assert sum(row["completed"] for row in outcomes) == 1
            with database.begin() as session:
                session.execute(text("DELETE FROM article_opens"))
                session.execute(
                    text("UPDATE articles SET automation_next_check_at=:now"),
                    {"now": now + timedelta(hours=1)},
                )
            await exporter.once()
            assert sum(row["opens"] for row in await _facts(client, "audience", now)) == 0
            with database() as session:
                assert session.scalar(text("SELECT count(*) FROM analytics_ingest_events")) == 0

    asyncio.run(verify())


@pytest.mark.integration
def test_clickhouse_outage_keeps_main_writes_committable_and_recovers_latest_facts(database):
    settings = _settings()
    first, second, account_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = utcnow()

    async def verify():
        async with AnalyticsClient(settings) as client:
            await _reset(client)
            exporter = AnalyticsExporter(client, settings)
            await _drain_initial(exporter, client)
            with database.begin() as session:
                session.add(_call(first, now))
            with database() as session:
                original_ids = set(session.scalars(text("SELECT id FROM analytics_ingest_events")))
            assert original_ids
            with socket.socket() as unavailable:
                unavailable.bind(("127.0.0.1", 0))
                offline = settings.model_copy(
                    update={
                        "clickhouse_url": f"http://127.0.0.1:{unavailable.getsockname()[1]}",
                        "clickhouse_connect_timeout_seconds": 0.1,
                        "clickhouse_timeout_seconds": 0.5,
                    }
                )
                async with AnalyticsClient(offline) as offline_client:
                    exporter.client = offline_client
                    with pytest.raises(AnalyticsUnavailable):
                        await exporter.once()
                    with database() as session:
                        assert (
                            set(session.scalars(text("SELECT id FROM analytics_ingest_events")))
                            == original_ids
                        )
                    with database.begin() as session:
                        session.execute(text("SET LOCAL statement_timeout='1s'"))
                        row = session.get(InferenceCall, first)
                        row.status, row.finished_at = "returned", now
                        row.tokens = {"inputTokens": 7, "outputTokens": 2}
                        session.add(_call(second, now))
                        session.add(
                            UserAccount(
                                id=account_id,
                                issuer="https://identity.test",
                                subject="outage-user",
                                organization_id="outage-org",
                                profile={},
                            )
                        )
                    with database() as session:
                        assert session.get(InferenceCall, first).status == "returned"
                        assert session.get(InferenceCall, second) is not None
                        assert session.get(UserAccount, account_id) is not None
                        pending = set(
                            session.scalars(text("SELECT id FROM analytics_ingest_events"))
                        )
                        assert original_ids < pending
                    with pytest.raises(AnalyticsUnavailable):
                        await exporter.once()
                    with database() as session:
                        assert (
                            set(session.scalars(text("SELECT id FROM analytics_ingest_events")))
                            == pending
                        )
            exporter.client = client
            await exporter.once()
            facts = await _facts(client, "inference", now)
            assert len(facts) == 2
            assert [row for row in facts if row["status"] == "returned"][0]["input_tokens"] == 7
            assert (
                sum(row["accounts_created"] for row in await _facts(client, "audience", now)) == 1
            )
            with database() as session:
                assert session.scalar(text("SELECT count(*) FROM analytics_ingest_events")) == 0
            await exporter.once()
            assert len(await _facts(client, "inference", now)) == 2

    asyncio.run(verify())


@pytest.mark.integration
def test_raw_decision_ledger_keeps_exact_usage_unknowns_and_transactional_corrections(database):
    settings = _settings()
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    proposal_id = uuid.uuid4()
    calls = [
        {"stage": "draft", "started_at": None, "tokens": {}, "charged_tokens": None},
        {
            "stage": "draft",
            "started_at": now.isoformat(),
            "tokens": {"inputTokens": 2**63 - 1, "outputTokens": 2**63 - 1},
            "charged_tokens": 7,
        },
    ]
    with database.begin() as session:
        session.add(
            TopicProposal(
                id=proposal_id,
                batch_id=uuid.uuid4(),
                slug=f"native-ledger-{proposal_id}",
                action="create",
                origin="import",
                source_name="Ledger fixture",
                proposed={},
                created_by={},
            )
        )
        session.flush()
        session.add(
            TopicDecisionRun(
                proposal_id=proposal_id,
                input_hash="fixture",
                status="deferred",
                reason="budget_exhausted",
                updated_at=now,
                state={"calls": calls},
            )
        )

    async def verify():
        async with AnalyticsClient(settings) as client:
            await _reset(client)
            exporter = AnalyticsExporter(client, settings)
            await _drain_initial(exporter, client)

            async def ledger():
                timed = await _facts(client, "decisions", now)
                undated = await _facts(client, "decisions", datetime(1970, 1, 2, tzinfo=UTC))
                return timed + undated

            rows = await ledger()
            assert len(rows) == 3
            assert sum(row["decision_tokens"] for row in rows) == 2**64 - 2
            assert [row for row in rows if row["ordinal"] == 1][0]["charged_tokens"] is None
            assert [row for row in rows if row["task"] == "decision-run"][0]["outcome"] == (
                "budget_exhausted"
            )
            # An append in an uncommitted source transaction must not leak into
            # the analytical ledger. Committing it publishes exactly one call.
            with psycopg.connect(postgres_dsn(settings)) as pending:
                extra = {
                    "stage": "verification",
                    "started_at": (now + timedelta(minutes=1)).isoformat(),
                    "tokens": {"totalTokens": 11},
                    "charged_tokens": 9,
                    "escalated": True,
                }
                pending.execute(
                    "UPDATE topic_decision_runs SET state=jsonb_set(state,'{calls}',"
                    "state->'calls'||%s::jsonb) WHERE proposal_id=%s",
                    (json.dumps([extra]), proposal_id),
                )
                await exporter.once()
                assert len(await ledger()) == 3
                pending.commit()
            await exporter.once()
            rows = await ledger()
            assert len(rows) == 4
            appended = [row for row in rows if row["ordinal"] == 3][0]
            assert appended["decision_tokens"] == 11 and appended["charged_tokens"] == 9
            assert appended["escalated"] == 1 and appended["parent_id"] == str(proposal_id)
            with database.begin() as session:
                run = session.get(TopicDecisionRun, proposal_id)
                run.state = {
                    "calls": [{**extra, "started_at": (now + timedelta(hours=2)).isoformat()}]
                }
            await exporter.once()
            rows = await ledger()
            assert len(rows) == 2 and sum(row["decision_tokens"] for row in rows) == 11
            with database.begin() as session:
                session.delete(session.get(TopicDecisionRun, proposal_id))
            await exporter.once()
            assert await ledger() == []
            raw = await client.query(
                "SELECT count() AS observations FROM analytics_fact_events WHERE domain='decisions'"
            )
            assert raw[0]["observations"] > 4
            assert await _facts(client, "inference", now) == []

    asyncio.run(verify())


@pytest.mark.integration
def test_sparse_bucket_batches_publish_complete_generations_and_bound_bootstrap_work(database):
    settings = _settings()
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    buckets = [now - timedelta(hours=3 * index) for index in range(REFRESH_BATCH_SIZE + 1)]

    async def verify():
        async with AnalyticsClient(settings) as client:
            await _reset(client)
            rows = []
            for index, bucket in enumerate(buckets):
                fact = empty_fact(
                    "inference",
                    uuid.uuid4(),
                    bucket,
                    event_id=index + 1,
                    captured_at=now,
                    generation=1,
                )
                fact.update(task="analysis", model="initial", status="returned", calls=1)
                rows.append(fact)
            await client.insert("analytics_fact_events", rows)
            publisher = RollupPublisher(client)
            async with await psycopg.AsyncConnection.connect(
                postgres_dsn(settings), autocommit=True, row_factory=dict_row
            ) as connection:
                await publisher.mark_dirty(connection, rows, 1)
                await connection.execute(
                    "UPDATE analytics_fact_refresh SET available_at=clock_timestamp()"
                )

                async def fence(conn):
                    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS

                assert await publisher.refresh_one(connection, 2, fence=fence)
                pending = await (
                    await connection.execute(
                        "SELECT count(*) AS pending FROM analytics_fact_refresh"
                    )
                ).fetchone()
                assert pending["pending"] == 1
                manifests = await client.query(
                    "SELECT count() AS count FROM analytics_bucket_manifest FINAL"
                )
                assert manifests[0]["count"] == REFRESH_BATCH_SIZE
                assert await publisher.refresh_one(connection, 3, fence=fence)
                assert not await publisher.refresh_one(connection, 4, fence=fence)

                # Changing the grouping dimension must hide the old model, and
                # deleting the last fact must publish an empty commit record.
                corrected = dict(rows[0], model="corrected", generation=5, event_id=100)
                deleted = dict(rows[1], deleted=1, generation=5, event_id=101)
                await client.insert("analytics_fact_events", [corrected, deleted])
                await publisher.mark_dirty(connection, [corrected, deleted], 5)
                await connection.execute(
                    "UPDATE analytics_fact_refresh SET available_at=clock_timestamp()"
                )
                assert await publisher.refresh_one(connection, 6, fence=fence)
                served = await client.query(
                    "SELECT r.model,sum(r.calls) AS calls FROM analytics_inference_hourly r "
                    "INNER JOIN (SELECT * FROM analytics_bucket_manifest FINAL) m "
                    "ON m.domain='inference' "
                    "AND r.bucket_hour=m.bucket AND r.generation=m.generation GROUP BY r.model"
                )
                assert {row["model"]: row["calls"] for row in served} == {
                    "initial": REFRESH_BATCH_SIZE - 1,
                    "corrected": 1,
                }
                assert (
                    await client.query(
                        "SELECT count() AS count FROM analytics_bucket_manifest FINAL"
                    )
                )[0]["count"] == len(buckets)

    asyncio.run(verify())


@pytest.mark.integration
def test_sorted_fact_merges_preserve_late_retractions_and_all_rollups_across_95_days():
    """Engine merging must match argMaxMerge before any mutable payload is filtered."""
    settings = _settings()
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    old = now - timedelta(days=94)
    start = now - timedelta(days=95)
    end = now.replace(hour=0) + timedelta(days=1)
    source_id, topic_id = str(uuid.uuid4()), str(uuid.uuid4())
    domains = ("publishing", "audience", "processing", "inference", "decisions")
    arguments = {
        "bucket": start,
        "end": end,
    }

    def fact(domain, identity, at, event_id):
        row = empty_fact(domain, identity, at, event_id=event_id, captured_at=now, generation=1)
        row.update(
            task=domain,
            model="original",
            effort="medium",
            status="returned",
            outcome="useful",
            source_ids=[source_id],
            topic_ids=[topic_id],
            viewer_key=str(identity),
            published=1,
            automated=1,
            opens=1,
            accounts_created=1,
            calls=1,
            completed=1,
            useful=1,
            input_tokens=17,
            duration_ms=1000,
            has_duration=1,
            delay_seconds=1.0,
            has_delay=1,
        )
        return row

    async def verify():
        database = f"devfeed_sorted_rollups_{uuid.uuid4().hex}_test"
        async with AnalyticsClient(
            settings.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"CREATE DATABASE `{database}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": database})
                ) as client:
                    await client.initialize()
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    # Keep duplicate/out-of-order states in separate physical parts.
                    # Only this test's private state table is affected.
                    await client.execute("SYSTEM STOP MERGES analytics_fact_state")
                    initial, corrected, late = [], [], []
                    for index, domain in enumerate(domains):
                        moved = fact(domain, uuid.uuid4(), old, index * 10 + 1)
                        retained = fact(domain, uuid.uuid4(), old, index * 10 + 2)
                        deleted = fact(domain, uuid.uuid4(), now, index * 10 + 3)
                        initial.extend(
                            [
                                moved,
                                retained,
                                deleted,
                                fact(domain, uuid.uuid4(), start - timedelta(hours=1), 100 + index),
                                fact(domain, uuid.uuid4(), end, 200 + index),
                            ]
                        )
                        corrected.extend(
                            [
                                dict(moved, deleted=1, generation=3, event_id=300 + index),
                                dict(
                                    moved,
                                    bucket_hour=now,
                                    event_time=now,
                                    model="corrected",
                                    published=2,
                                    automated=2,
                                    opens=2,
                                    accounts_created=2,
                                    calls=2,
                                    completed=2,
                                    useful=2,
                                    input_tokens=2**64 + 9,
                                    generation=3,
                                    event_id=400 + index,
                                ),
                                dict(deleted, deleted=1, generation=4, event_id=500 + index),
                            ]
                        )
                        late.extend([moved, dict(deleted, generation=2)])
                    await client.insert("analytics_fact_events", initial)
                    await client.insert("analytics_fact_events", initial)
                    await client.insert("analytics_fact_events", corrected)
                    await client.insert("analytics_fact_events", late)
                    columns = ",".join(
                        f"tupleElement(fact,{position}) AS `{name}`"
                        for position, name in enumerate(PAYLOAD_NAMES, 1)
                    )
                    for domain in domains:
                        oracle = (
                            f"SELECT bucket_hour,fact_id,{columns} FROM ("
                            "SELECT bucket_hour,fact_id,argMaxMerge(state) AS fact "
                            f"FROM analytics_fact_state WHERE domain='{domain}' "
                            "AND bucket_hour>={bucket:DateTime64(6,'UTC')} "
                            "AND bucket_hour<{end:DateTime64(6,'UTC')} "
                            "GROUP BY domain,bucket_hour,fact_id) WHERE tupleElement(fact,1)=0 "
                            "ORDER BY bucket_hour,fact_id"
                        )
                        expected = await client.query(oracle, arguments)
                        actual = await client.query(
                            canonical_sql(domain) + " ORDER BY bucket_hour,fact_id", arguments
                        )
                        assert actual == expected and len(actual) == 2
                        assert sum(row["calls"] for row in actual) == 3
                        assert sum(row["input_tokens"] for row in actual) == 2**64 + 26
                    for domain in (*domains[:4], "sources", "topics"):
                        buckets = [old, now]
                        if domain in {"sources", "topics"}:
                            buckets = [bucket.replace(hour=0) for bucket in buckets]
                        await client.execute(
                            rebuild_sql(domain, batch=True)
                            + " SETTINGS optimize_aggregation_in_order=1",
                            {
                                "buckets": [
                                    bucket.strftime("%Y-%m-%d %H:%M:%S") for bucket in buckets
                                ],
                                "generation": 7,
                            },
                        )
                        table, counter = {
                            "publishing": ("publishing_hourly", "published"),
                            "audience": ("audience_hourly", "opens"),
                            "processing": ("processing_hourly", "completed"),
                            "inference": ("inference_hourly", "calls"),
                            "sources": ("source_daily", "published"),
                            "topics": ("topic_daily", "published"),
                        }[domain]
                        totals = await client.query(
                            f"SELECT sum({counter}) AS total FROM analytics_{table}"
                        )
                        assert totals == [{"total": 3}]
                    assert await client.query(
                        "SELECT sum(input_tokens) AS tokens FROM analytics_inference_hourly"
                    ) == [{"tokens": 2**64 + 26}]
            finally:
                await admin.execute(f"DROP DATABASE `{database}` SYNC")

    asyncio.run(verify())
