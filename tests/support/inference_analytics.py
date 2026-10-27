"""Disposable ClickHouse database for direct inference integration tests."""

import uuid
from contextlib import asynccontextmanager

from devfeed_core.analytics import AnalyticsClient

from tests.support.analytics import settings


@asynccontextmanager
async def inference_client():
    configured = settings()
    database = f"inference_{uuid.uuid4().hex}_test"
    async with AnalyticsClient(configured) as admin:
        await admin.execute(f"CREATE DATABASE `{database}`")
        try:
            async with AnalyticsClient(
                configured.model_copy(update={"clickhouse_database": database})
            ) as client:
                client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                await client.initialize()
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
                yield client
        finally:
            await admin.execute(f"DROP DATABASE `{database}` SYNC")
