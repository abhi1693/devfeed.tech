"""Native analytical fixtures using the caller's visible transaction snapshot.

Production delivery is exercised separately by exporter integration tests. These
builders publish typed facts and complete compact cohorts in a unique test DB.
"""

import asyncio
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, timedelta
from types import SimpleNamespace

import pytest
from devfeed_admin_api.analytics_reports import Reports
from devfeed_admin_api.overview_windows import recent_window
from devfeed_core.analytics import AnalyticsClient
from devfeed_core.analytics_facts import dirty_buckets
from devfeed_core.analytics_gauges import Gauges, shard
from devfeed_core.analytics_rollups import canonical_sql, rebuild_sql
from devfeed_core.analytics_schema import TABLES
from devfeed_core.analytics_transform import FactTransformer
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
    """Read source facts in this test's existing, potentially uncommitted transaction."""

    def __init__(self, session):
        self.session = session

    async def execute(self, statement, parameters=()):
        with self.session.connection().connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(statement, parameters)
            rows = cursor.fetchall()

        async def fetchall():
            return rows

        return SimpleNamespace(fetchall=fetchall)


async def _snapshot(client, session, now):
    await client.initialize()
    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
    session.flush()
    events: list[dict] = []
    for source, table in TABLES.items():
        for record in session.scalars(text(f'SELECT {table.record_sql("r")} FROM "{source}" r')):
            events.append(
                dict(
                    id=len(events) + 1,
                    source_table=source,
                    operation="I",
                    record=record,
                    before_record=None,
                    created_at=now,
                )
            )
    connection = SnapshotConnection(session)
    facts = await FactTransformer(connection).transform(events, 1)
    await client.insert("analytics_fact_events", facts)
    for bucket in dirty_buckets(facts, 1):
        end = (
            bucket["bucket"] + timedelta(days=1)
            if bucket["domain"] in {"sources", "topics"}
            else bucket["bucket"] + timedelta(hours=1)
        )
        await client.execute(rebuild_sql(bucket["domain"]), {**bucket, "end": end})
        await client.insert(
            "analytics_bucket_manifest", [{**bucket, "updated_at": now, "lag_seconds": 0}]
        )
    gauge = Gauges(settings(), client)
    await gauge.ingest(connection, events, generation=1)
    keys = {("feed", 0), ("sources", 0), ("topics", 0), ("queues", 0)}
    for event in events:
        source, record = event["source_table"], event["record"]
        if source in {"user_accounts", "user_recommendation_states"}:
            keys.add(("feed", shard(record.get("user_id") or record["id"])))
        elif source in {"sources", "topics"}:
            keys.add((source, shard(record["id"])))
        elif source in {"user_topics", "user_interests"}:
            keys.add(("topics", shard(record["topic_id"])))
    for domain, number in sorted(keys):
        if domain == "queues":
            records, _ = await gauge._queues(connection)
            target = "queue"
        elif domain == "feed":
            records, _ = await gauge._feed(number)
            target = "feed"
        else:
            records, _ = await gauge._dimensions(domain, number)
            target = "source" if domain == "sources" else "topic"
        await client.insert(
            f"analytics_{target}_gauges",
            [{**record, "generation": 1, "shard": number} for record in records],
        )
        await client.insert(
            "analytics_gauge_manifest",
            [dict(domain=domain, shard=number, generation=1, updated_at=now)],
        )


def _read(session_or_factory, now, read):
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
                        await _snapshot(client, session, now)
                        return await read(client, now)
                finally:
                    await admin.execute(f"DROP DATABASE `{database}` SYNC")

        return asyncio.run(run())


def load_section(session_or_factory, section, days, now, *, window=None):
    async def read(client, now):
        return await Reports(
            client, window or recent_window(days, now), now, capture_started_at=now
        ).section(section)

    return _read(session_or_factory, now, read)


def inference_rows(session_or_factory, start, end):
    async def read(client, now):
        lower = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
        begin = lower.replace(minute=0, second=0, microsecond=0)
        finish = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        source = canonical_sql("inference")
        return await client.query(
            "SELECT toString(toDate(event_time)) AS date,task AS operation,model,effort,"
            "sum(calls) AS calls,countIf(status='returned') AS returned,"
            "sum(failed) AS failed,countIf(status='running') AS running,"
            "sum(unreported) AS unreported,sum(input_tokens) AS input_tokens,"
            "sum(cached_input_tokens) AS cached_input_tokens,sum(output_tokens) AS output_tokens,"
            "sum(reasoning_tokens) AS reasoning_tokens,sum(web_searches) AS web_searches,"
            "sum(duration_ms) AS duration_ms FROM (" + source + ") "
            "WHERE event_time>={start:DateTime64(6,'UTC')} "
            "AND event_time<={now:DateTime64(6,'UTC')} "
            "GROUP BY date,operation,model,effort ORDER BY date,operation,model,effort",
            {"start": lower, "now": now, "bucket": begin, "end": finish},
        )

    return _read(session_or_factory, end, read)


def load_sections(session_or_factory, sections, days, now, *, window=None):
    async def read(client, now):
        reports = Reports(client, window or recent_window(days, now), now, capture_started_at=now)
        return {name: await reports.section(name) for name in sections}

    return _read(session_or_factory, now, read)
