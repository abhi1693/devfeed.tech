"""Copy and verify the active inference accounting window after old workers drain."""

import asyncio

from devfeed_core.analytics import AnalyticsClient
from devfeed_core.config import get_settings
from devfeed_core.inference_ledger import QUOTA_LOCK, UPSERT, InferenceLedger, ledger_values
from devfeed_core.inference_observation import utc_timestamp


async def import_history(source: AnalyticsClient, ledger: InferenceLedger) -> int:
    async with await ledger.connect() as conn:
        await conn.execute(QUOTA_LOCK)
        cutoff = await (
            await conn.execute("SELECT statement_timestamp() - interval '24 hours' AS cutoff")
        ).fetchone()
        assert cutoff is not None
        progress = await source.query(
            "SELECT complete FROM analytics_backfill_progress FINAL "
            "WHERE import_id='direct-v1' AND source='inference_calls'"
        )
        if progress != [{"complete": 1}]:
            raise RuntimeError("Historical inference import is incomplete")
        rows = await source.query(
            """SELECT id,started_at,revision,task,model,effort,model_is_null,model_is_empty,
                effort_is_null,effort_is_empty,reason,status,input_tokens,cached_input_tokens,
                reported_cached_input_tokens,cache_write_input_tokens,has_input_tokens,
                output_tokens,reasoning_tokens,web_searches,duration_ms,has_duration
                FROM analytics_inference_events FINAL
                WHERE started_at >= {cutoff:DateTime64(6,'UTC')}
                SETTINGS optimize_move_to_prewhere_if_final=0""",
            {"cutoff": cutoff["cutoff"]},
        )
        await conn.execute(
            "CREATE TEMP TABLE imported_inference (LIKE inference_usage_ledger "
            "INCLUDING INDEXES) ON COMMIT DROP"
        )
        async with conn.cursor() as cursor:
            for row in rows:
                row["started_at"] = utc_timestamp(row["started_at"])
                await cursor.execute(
                    "INSERT INTO imported_inference VALUES (%s,%s,%s,%s,%s)", ledger_values(row)
                )
                await cursor.execute(UPSERT, ledger_values(row))
        mismatch = await (
            await conn.execute("""
            SELECT count(*) AS n FROM imported_inference i
            LEFT JOIN inference_usage_ledger l USING (started_at,id)
            WHERE l.id IS NULL OR l.task <> i.task OR l.revision < i.revision OR
                  (l.revision = i.revision AND l.usage <> i.usage)
        """)
        ).fetchone()
        assert mismatch is not None
        if mismatch["n"]:
            raise RuntimeError("PostgreSQL inference usage differs from the source")
        await conn.execute("""INSERT INTO inference_quota_cutover VALUES
            (true,statement_timestamp())
            ON CONFLICT (singleton) DO UPDATE SET verified_at=excluded.verified_at""")
    return len(rows)


async def main() -> None:
    async with AnalyticsClient() as source:
        count = await import_history(source, InferenceLedger(get_settings()))
        print(f"Verified {count} inference records")


if __name__ == "__main__":
    asyncio.run(main())
