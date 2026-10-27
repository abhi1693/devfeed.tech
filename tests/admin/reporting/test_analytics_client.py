"""Native analytics transport bounds connections, inserts, memory, and failures."""

import asyncio
import json
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from clickhouse_connect.driver.exceptions import DatabaseError
from devfeed_core import analytics
from devfeed_core.config import Settings


def configuration(**values):
    return Settings(
        _env_file=None,
        database_url="postgresql://unit@database.invalid/unit_test",
        redis_url="redis://redis.invalid/15",
        **values,
    )


def driver():
    return SimpleNamespace(
        command=AsyncMock(),
        raw_query=AsyncMock(return_value=b""),
        insert=AsyncMock(),
        close=AsyncMock(),
    )


def test_lazy_native_connection_is_shared_and_closed_once_under_concurrent_queries(monkeypatch):
    async def run():
        native = driver()

        async def connect(**_kwargs):
            await asyncio.sleep(0.001)
            return native

        connect = AsyncMock(side_effect=connect)
        monkeypatch.setattr(analytics.clickhouse_connect, "get_async_client", connect)
        async with analytics.AnalyticsClient(configuration()) as client:
            await asyncio.gather(*(client.execute("SELECT 1") for _ in range(6)))
            assert native.command.await_count == 6
            assert connect.await_count == 1
            options = connect.call_args.kwargs
            assert options["autogenerate_session_id"] is False
            assert options["show_clickhouse_errors"] is False
            assert options["connector_limit"] == options["connector_limit_per_host"] == 2
        await client.close()
        native.close.assert_awaited_once()

    asyncio.run(run())


def test_query_encodes_parameters_without_interpolating_sql_and_preserves_numeric_results():
    async def run():
        native = driver()
        native.raw_query.return_value = b'{"count":18446744073709551615}\n\n'
        identity = uuid.uuid4()
        stamp = datetime(2026, 10, 27, 12, 34, 56, 123456, tzinfo=timezone(timedelta(hours=2)))
        parameters = {
            "when": stamp,
            "day": stamp.date(),
            "nested": [identity, (1, None, True)],
            "object": {"when": stamp, "ratio": 1.5},
        }
        external = object()
        async with analytics.AnalyticsClient(configuration(), driver=native) as client:
            result = await client.query(
                "SELECT {when:DateTime64(6)}; \n", parameters, external_data=external
            )
            assert result == [{"count": 2**64 - 1}]
            query = native.raw_query.call_args
            assert query.args == ("SELECT {when:DateTime64(6)}",)
            assert query.kwargs["parameters"] == {
                "when": "2026-10-27 10:34:56.123456",
                "day": "2026-10-27",
                "nested": [str(identity), [1, None, True]],
                "object": {"when": "2026-10-27 10:34:56.123456", "ratio": 1.5},
            }
            assert query.kwargs["external_data"] is external
            assert query.kwargs["fmt"] == "JSONEachRow"
            assert (
                query.kwargs["settings"]["max_memory_usage"]
                == client.settings.clickhouse_max_memory_bytes
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [DatabaseError("private query"), aiohttp.ClientConnectionError("private URL"), TimeoutError()],
)
def test_native_failures_expose_a_stable_safe_service_error(error):
    async def run():
        native = driver()
        native.command.side_effect = error
        async with analytics.AnalyticsClient(configuration(), driver=native) as client:
            with pytest.raises(
                analytics.AnalyticsUnavailable, match="^ClickHouse request failed$"
            ) as failure:
                await client.execute("SELECT secret")
            assert failure.value.__cause__ is error

    asyncio.run(run())


def test_native_deadline_includes_connection_admission_and_insert_schema_discovery():
    async def run():
        native = driver()
        client = analytics.AnalyticsClient(
            configuration(clickhouse_timeout_seconds=0.01, clickhouse_max_concurrent_queries=1),
            driver=native,
        )
        await client._semaphore.acquire()
        try:
            with pytest.raises(analytics.AnalyticsUnavailable):
                await client.execute("SELECT 1")
            native.command.assert_not_awaited()
        finally:
            client._semaphore.release()

        async def describe(*_args, **_kwargs):
            await asyncio.sleep(10)

        client.query = describe
        with pytest.raises(analytics.AnalyticsUnavailable):
            await client.insert("analytics_events", [{"id": 1}])
        native.insert.assert_not_awaited()
        await client.close()

    asyncio.run(run())


def test_native_insert_converts_typed_columns_and_coalesces_schema_discovery():
    async def run():
        native = driver()
        columns = {
            "id": "Nullable(UUID)",
            "when": "DateTime64(6,'UTC')",
            "day": "Date32",
            "history": "Array(Nullable(DateTime64(6,'UTC')))",
            "readers": "Array(UUID)",
            "labels": "LowCardinality(String)",
            "count": "UInt64",
        }

        async def describe(statement, **_kwargs):
            assert statement == "DESCRIBE TABLE analytics_article_opens"
            await asyncio.sleep(0.001)
            return (
                "\n".join(
                    json.dumps({"name": key, "type": value}) for key, value in columns.items()
                )
                + "\n"
            ).encode()

        native.raw_query.side_effect = describe
        identity = uuid.uuid4()
        stamp = datetime(2026, 10, 27, 12, 0)
        rows = [
            {
                "id": identity,
                "when": stamp,
                "day": "2026-10-27",
                "history": ["2026-10-27T12:00:00Z", None],
                "readers": [identity],
                "labels": "public",
                "count": 4,
            },
            {
                "id": None,
                "when": "2026-10-27T12:00:00+00:00",
                "day": stamp,
                "history": None,
                "readers": [],
                "labels": "other",
                "count": 0,
            },
        ]
        async with analytics.AnalyticsClient(configuration(), driver=native) as client:
            await asyncio.gather(
                *(client.insert("analytics_article_opens", rows) for _ in range(3))
            )
            await client.insert("analytics_article_opens", rows)
            assert native.raw_query.await_count == 1
            assert native.insert.await_count == 4
            call = native.insert.call_args.kwargs
            actual = dict(zip(call["column_names"], call["data"], strict=True))
            assert actual["id"] == [str(identity), None]
            assert actual["when"] == [stamp.replace(tzinfo=UTC)] * 2
            assert actual["day"] == [date(2026, 10, 27)] * 2
            assert actual["history"] == [[stamp.replace(tzinfo=UTC), None], None]
            assert actual["readers"] == [[str(identity)], []]
            assert actual["count"] == [4, 0] and actual["labels"] == ["public", "other"]
            assert call["column_oriented"] is True
            assert (
                call["settings"]["async_insert"] == call["settings"]["wait_for_async_insert"] == 1
            )
            assert (
                call["settings"]["insert_deduplicate"]
                == call["settings"]["async_insert_deduplicate"]
                == 0
            )
            assert len({id(call.kwargs["data"]) for call in native.insert.await_args_list}) == 4

    asyncio.run(run())


@pytest.mark.parametrize(
    "table,rows",
    [
        ("analytics_events;DROP", [{"id": 1}]),
        ("other_table", [{"id": 1}]),
        ("analytics_events", [{}]),
        ("analytics_events", [{"id": 1}, {"other": 2}]),
    ],
)
def test_invalid_insert_never_opens_a_driver_or_builds_a_schema(table, rows):
    async def run():
        native = driver()
        async with analytics.AnalyticsClient(configuration(), driver=native) as client:
            with pytest.raises(ValueError):
                await client.insert(table, rows)
            await client.insert("analytics_events", [])
            native.raw_query.assert_not_awaited()
            native.insert.assert_not_awaited()

    asyncio.run(run())


def test_invalid_native_json_is_an_explicit_unavailable_result():
    async def run():
        native = driver()
        native.raw_query.return_value = b"not JSON"
        async with analytics.AnalyticsClient(configuration(), driver=native) as client:
            with pytest.raises(analytics.AnalyticsUnavailable, match="invalid analytical result"):
                await client.query("SELECT 1")

    asyncio.run(run())


def test_initialize_creates_every_active_store_and_status_uses_direct_readiness_only():
    async def run():
        native = driver()
        native.raw_query.return_value = (
            b'{"version":1,"ready":1,"updated_at":"2026-10-27T12:00:00Z"}\n'
        )
        async with analytics.AnalyticsClient(configuration(), driver=native) as client:
            await client.initialize()
            ddl = "\n".join(call.args[0] for call in native.command.await_args_list)
            for target in (
                "analytics_article_opens",
                "analytics_open_lifetime",
                "analytics_open_audience_hourly",
                "analytics_inference_events",
                "analytics_partner_events",
                "analytics_card_canonical",
                "analytics_pipeline_events",
                "analytics_direct_status",
            ):
                assert target in ddl
            assert "analytics_fact_events" not in ddl
            status = await client.status()
            assert status["generated_at"] == status["updated_at"] and status["ready"] == 1
            assert "analytics_direct_status" in native.raw_query.call_args.args[0]

    asyncio.run(run())


@pytest.mark.parametrize(
    "value,expected",
    [
        (datetime(2026, 10, 27, tzinfo=UTC), datetime(2026, 10, 27, tzinfo=UTC)),
        (
            datetime(2026, 10, 27, tzinfo=timezone(timedelta(hours=2))),
            datetime(2026, 10, 26, 22, tzinfo=UTC),
        ),
        (None, None),
        (123, 123),
    ],
)
def test_native_datetime_coercion_keeps_nulls_and_normalizes_aware_inputs(value, expected):
    assert analytics._insert_datetime(value) == expected


def test_native_dates_and_nested_low_cardinality_arrays_keep_declared_types():
    stamp = datetime(2026, 10, 27, tzinfo=UTC)
    assert analytics._insert_date(stamp) == stamp.date()
    assert analytics._insert_date(stamp.date()) == stamp.date()
    assert analytics._insert_converter("LowCardinality(Nullable(DateTime64(6)))")(None) is None
    assert analytics._insert_converter("Array(UInt64)") is None
