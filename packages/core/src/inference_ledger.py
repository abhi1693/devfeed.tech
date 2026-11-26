"""PostgreSQL accounting for content-free inference and rolling relationship-call budgets."""

import asyncio
from datetime import datetime

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from devfeed_core.config import Settings
from devfeed_core.inference_observation import observation
from devfeed_core.json_types import JsonRecord

RELATIONSHIP_TASKS = ("relationship_research", "relationship_verification")
# Same shared transaction lock as the ClickHouse-backed 0.1.8 workers.
QUOTA_LOCK = "SELECT pg_advisory_xact_lock(4474450, 0)"
UPSERT = """
    INSERT INTO inference_usage_ledger (started_at,id,task,revision,usage)
    VALUES (%s,%s,%s,%s,%s)
    ON CONFLICT (started_at,id) DO UPDATE
    SET revision = EXCLUDED.revision, usage = EXCLUDED.usage
    WHERE inference_usage_ledger.revision < EXCLUDED.revision
      AND inference_usage_ledger.task = EXCLUDED.task
"""


def ledger_values(row: JsonRecord) -> tuple[datetime, str, str, int, Jsonb]:
    usage = {
        key: value
        for key, value in row.items()
        if key not in {"id", "started_at", "task", "revision"}
    }
    return row["started_at"], row["id"], row["task"], row["revision"], Jsonb(usage)


class InferenceLedger:
    def __init__(self, settings: Settings) -> None:
        self.dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self.connect_timeout = settings.database_connect_timeout_seconds

    async def connect(self) -> AsyncConnection[JsonRecord]:
        return await AsyncConnection.connect(
            self.dsn, connect_timeout=self.connect_timeout, row_factory=dict_row
        )

    async def write(self, values: JsonRecord) -> None:
        parameters = ledger_values(observation(values))
        async with await self.connect() as conn:
            await conn.execute(UPSERT, parameters)

    async def admit(self, values: JsonRecord, limit: int | None) -> bool:
        """Return success only after the counted reservation commits."""
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("Invalid relationship budget")
        row = observation(values)
        if row["status"] != "running" or row["task"] not in RELATIONSHIP_TASKS:
            raise ValueError("Invalid relationship reservation")
        async with asyncio.timeout(30), await self.connect() as conn:
            await conn.execute("SET LOCAL statement_timeout = '30s'")
            await conn.execute(QUOTA_LOCK)
            if limit is not None:
                counted = await (
                    await conn.execute("""
                    SELECT count(*) AS calls FROM inference_usage_ledger
                    WHERE started_at >= statement_timestamp() - interval '24 hours'
                      AND task IN ('relationship_research','relationship_verification')
                    HAVING EXISTS (SELECT 1 FROM inference_quota_cutover WHERE singleton)
                """)
                ).fetchone()
                if counted is None or counted["calls"] >= limit:
                    return False
            inserted = await conn.execute(
                """INSERT INTO inference_usage_ledger (started_at,id,task,revision,usage)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                ledger_values(row),
            )
            return await inserted.fetchone() is not None
