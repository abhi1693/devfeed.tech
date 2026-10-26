"""Bounded native asynchronous ClickHouse Connect access for analytical workloads."""

import asyncio
import json
import re
from datetime import UTC, date, datetime
from typing import Any

import aiohttp
import clickhouse_connect
from clickhouse_connect.driver.exceptions import Error as ClickHouseError

from devfeed_core.analytics_facts import initialization_sql
from devfeed_core.config import Settings, get_settings


class AnalyticsUnavailable(RuntimeError):
    """Analytics cannot currently serve a complete result."""


def _parameter(value):
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_parameter(item) for item in value]
    if isinstance(value, dict):
        return {key: _parameter(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class AnalyticsClient:
    def __init__(self, settings: Settings | None = None, *, driver=None):
        self.settings = settings or get_settings()
        self._semaphore = asyncio.Semaphore(self.settings.clickhouse_max_concurrent_queries)
        self._connect_lock = asyncio.Lock()
        self._driver = driver
        self.insert_settings: dict[str, Any] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.close()

    async def close(self):
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    def _limits(self):
        return {
            "max_threads": self.settings.clickhouse_max_threads,
            "max_memory_usage": self.settings.clickhouse_max_memory_bytes,
            "max_execution_time": self.settings.clickhouse_max_execution_seconds,
            "date_time_input_format": "best_effort",
            "date_time_output_format": "iso",
            "output_format_json_quote_64bit_integers": 0,
            "join_use_nulls": 1,
            "prefer_column_name_to_alias": 1,
        }

    async def _get_driver(self):
        async with self._connect_lock:
            if self._driver is None:
                self._driver = await clickhouse_connect.get_async_client(
                    dsn=self.settings.clickhouse_url,
                    database=self.settings.clickhouse_database,
                    username=self.settings.clickhouse_username,
                    password=self.settings.clickhouse_password.get_secret_value(),
                    connect_timeout=self.settings.clickhouse_connect_timeout_seconds,
                    send_receive_timeout=self.settings.clickhouse_timeout_seconds,
                    connector_limit=self.settings.clickhouse_max_concurrent_queries,
                    connector_limit_per_host=self.settings.clickhouse_max_concurrent_queries,
                    autogenerate_session_id=False,
                    show_clickhouse_errors=False,
                )
        return self._driver

    async def _operation(self, method, *args, **kwargs):
        try:
            # Includes pool admission and lazy driver initialization, so a queue
            # cannot make the complete analytical request wait without a deadline.
            async with asyncio.timeout(self.settings.clickhouse_timeout_seconds):
                async with self._semaphore:
                    driver = await self._get_driver()
                    return await getattr(driver, method)(*args, **kwargs)
        except (ClickHouseError, aiohttp.ClientError, TimeoutError) as error:
            raise AnalyticsUnavailable("ClickHouse request failed") from error

    async def execute(self, sql: str, parameters: dict[str, Any] | None = None):
        await self._operation(
            "command",
            sql,
            parameters={key: _parameter(value) for key, value in (parameters or {}).items()},
            settings=self._limits(),
        )

    async def query(
        self, sql: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        response = await self._operation(
            "raw_query",
            sql.rstrip("; \n"),
            parameters={key: _parameter(value) for key, value in (parameters or {}).items()},
            fmt="JSONEachRow",
            settings=self._limits(),
        )
        try:
            return [json.loads(line) for line in response.splitlines() if line]
        except (ValueError, TypeError) as error:
            raise AnalyticsUnavailable(
                "ClickHouse returned an invalid analytical result"
            ) from error

    async def initialize(self):
        from devfeed_core.analytics_cards import card_initialization_sql
        from devfeed_core.analytics_gauges import Gauges

        for statement in initialization_sql():
            await self.execute(statement)
        await Gauges(self.settings, self).initialize()
        for statement in card_initialization_sql():
            await self.execute(statement)

    async def insert(self, table: str, rows: list[dict[str, Any]]):
        if not rows:
            return
        if re.fullmatch(r"analytics_[a-z][a-z0-9_]*", table) is None:
            raise ValueError("Unknown analytical table")
        data = "\n".join(json.dumps(row, default=_parameter, separators=(",", ":")) for row in rows)
        settings = (
            self._limits()
            | self.insert_settings
            | {
                "async_insert": 1,
                "wait_for_async_insert": 1,
            }
        )
        if table in {
            "analytics_fact_events",
            "analytics_audience_changes",
            "analytics_dimension_changes",
            "analytics_membership_changes",
        }:
            # argMax materialized states are idempotent; replay must rerun the
            # projection even after an earlier partial materialized-view failure.
            settings.update(insert_deduplicate=0, async_insert_deduplicate=0)
        await self._operation(
            "raw_insert",
            table=table,
            insert_block=data.encode(),
            fmt="JSONEachRow",
            settings=settings,
        )

    async def status(self) -> dict[str, Any]:
        rows = await self.query("SELECT * FROM analytics_pipeline_status FINAL")
        if not rows:
            raise AnalyticsUnavailable("Analytics initial backfill is still running")
        status = rows[0]
        updated_at = datetime.fromisoformat(status["updated_at"].replace("Z", "+00:00"))
        status["lag_seconds"] = max(
            float(status["lag_seconds"]), (datetime.now(UTC) - updated_at).total_seconds(), 0.0
        )
        return status
