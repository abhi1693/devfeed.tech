"""Direct ClickHouse fixtures over a caller's visible business transaction.

Production after-commit delivery is tested separately. These fixtures publish
sanitized current source snapshots and explicit historical observations into a
unique disposable analytical namespace.
"""

import asyncio
import os
import time
import uuid
from contextlib import contextmanager
from datetime import UTC
from types import SimpleNamespace

import pytest
from devfeed_admin_api.analytics_reports import Reports
from devfeed_admin_api.overview_windows import recent_window
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_article_opens import ArticleOpenAnalytics
from devfeed_core.analytics_backfill import BACKFILL_DDL
from devfeed_core.analytics_inference_store import InferenceStore
from devfeed_core.analytics_observation import pipeline_policy_version
from devfeed_core.analytics_overview_direct import DirectOverviewAnalytics
from devfeed_core.analytics_producer import SOURCES, TABLES, hydrate_changes
from devfeed_core.analytics_status import AnalyticsStatus
from devfeed_core.config import Settings
from psycopg.rows import dict_row
from sqlalchemy import text
from sqlalchemy.orm import Session


def settings():
    endpoint = os.environ.get("DEVFEED_TEST_CLICKHOUSE_URL")
    if not endpoint:
        pytest.skip("Set DEVFEED_TEST_CLICKHOUSE_URL for native analytical fixtures")
    database = os.environ.get("DEVFEED_TEST_CLICKHOUSE_DATABASE", "devfeed_test")
    if not database.endswith("_test"):
        pytest.fail("Analytical fixture database must end with _test")
    return Settings(
        _env_file=None,
        database_url=os.environ["DEVFEED_TEST_DATABASE_URL"],
        redis_url=os.environ["DEVFEED_TEST_REDIS_URL"],
        clickhouse_url=endpoint,
        clickhouse_database=database,
        clickhouse_username=os.environ.get("DEVFEED_TEST_CLICKHOUSE_USERNAME", "default"),
        clickhouse_password=os.environ.get("DEVFEED_TEST_CLICKHOUSE_PASSWORD", ""),
    )


@contextmanager
def _session(value):
    if isinstance(value, Session):
        yield value
    else:
        with value() as session:
            yield session


class SnapshotConnection:
    """Use the test's existing transaction, including its uncommitted source rows."""

    def __init__(self, session):
        self.session = session

    async def execute(self, statement, parameters=()):
        with self.session.connection().connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(statement, parameters)
            rows = cursor.fetchall()

        async def fetchall():
            return rows

        return SimpleNamespace(fetchall=fetchall)


async def prepare(client, now):
    await client.initialize()
    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
    await client.execute(BACKFILL_DDL)
    await client.execute(
        "CREATE TABLE IF NOT EXISTS analytics_audience_archive_daily (day Date,"
        "opens Nullable(UInt64),readers Nullable(Float64),accounts_created Nullable(UInt64),"
        "published Nullable(UInt64),version UInt64) "
        "ENGINE=ReplacingMergeTree(version) ORDER BY day"
    )
    await client.insert(
        "analytics_backfill_progress",
        [
            {
                "import_id": "direct-v1",
                "source": "inference_calls",
                "page": 1,
                "cursor": "",
                "complete": 1,
                "boundary": "",
            }
        ],
    )
    await AnalyticsStatus(client).write(
        ready=True,
        cutover=now,
        policy=pipeline_policy_version(client.settings),
    )


async def _snapshot(client, session, now, *, observations=None):
    await prepare(client, now)
    session.flush()
    changes = []
    for source in sorted(SOURCES):
        table = TABLES[source]
        for record in session.scalars(text(f'SELECT {table.record_sql("r")} FROM "{source}" r')):
            changes.append({"source_table": source, "record": record})
    connection = SnapshotConnection(session)
    overview = DirectOverviewAnalytics(client)
    revision = time.time_ns()
    for offset in range(0, len(changes), 200):
        hydrated = await hydrate_changes(connection, changes[offset : offset + 200])
        await overview.publish(hydrated, revision=revision)
    observations = observations or {}
    for kind, store in (
        ("inference", InferenceStore(client).write),
        ("opens", ArticleOpenAnalytics(client).import_opens),
    ):
        records = observations.get(kind, [])
        for offset in range(0, len(records), 1000):
            await store(records[offset : offset + 1000])


def _read(session_or_factory, now, read, *, observations=None):
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    configured = settings()
    with _session(session_or_factory) as session:

        async def run():
            database = f"devfeed_fixture_{uuid.uuid4().hex}_test"
            async with AnalyticsClient(configured) as admin:
                await admin.execute(f"CREATE DATABASE `{database}`")
                try:
                    async with AnalyticsClient(
                        configured.model_copy(update={"clickhouse_database": database})
                    ) as client:
                        await _snapshot(client, session, now, observations=observations)
                        return await read(client, now)
                finally:
                    await admin.execute(f"DROP DATABASE `{database}` SYNC")

        return asyncio.run(run())


def load_section(session_or_factory, section, days, now, *, window=None, observations=None):
    async def read(client, now):
        return await Reports(
            client, window or recent_window(days, now), now, capture_started_at=now
        ).section(section)

    return _read(session_or_factory, now, read, observations=observations)


def load_sections(session_or_factory, sections, days, now, *, window=None, observations=None):
    async def read(client, now):
        reports = Reports(client, window or recent_window(days, now), now, capture_started_at=now)
        return {name: await reports.section(name) for name in sections}

    return _read(session_or_factory, now, read, observations=observations)
