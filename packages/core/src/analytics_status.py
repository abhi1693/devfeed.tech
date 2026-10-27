"""One ClickHouse readiness record for the direct analytical stores."""

import time
from datetime import UTC, datetime

IMPORT_ID = "direct-v1"
STATUS_DDL = """CREATE TABLE IF NOT EXISTS analytics_direct_status (
    import_id String,version UInt64,ready UInt8,updated_at DateTime64(6,'UTC'),
    capture_started_at DateTime64(6,'UTC'),reader_coverage_start Date,lag_seconds Float64,
    policy String
) ENGINE=ReplacingMergeTree(version) ORDER BY import_id"""


class AnalyticsStatus:
    def __init__(self, client):
        self.client = client

    async def initialize(self):
        await self.client.execute(STATUS_DDL)

    async def write(self, *, ready, cutover, policy):
        if isinstance(cutover, str):
            cutover = datetime.fromisoformat(cutover.replace("Z", "+00:00"))
        now = datetime.now(UTC)
        await self.client.insert(
            "analytics_direct_status",
            [
                {
                    "import_id": IMPORT_ID,
                    "version": time.time_ns(),
                    "ready": int(ready),
                    "updated_at": now,
                    "capture_started_at": cutover,
                    "reader_coverage_start": cutover.date(),
                    "lag_seconds": 0.0,
                    "policy": policy,
                }
            ],
        )

    async def read(self):
        from devfeed_core.analytics import AnalyticsUnavailable

        rows = await self.client.query(
            "SELECT version,ready,updated_at,capture_started_at,reader_coverage_start,"
            "lag_seconds,policy "
            "FROM analytics_direct_status FINAL WHERE import_id={import_id:String}",
            {"import_id": IMPORT_ID},
        )
        if len(rows) != 1:
            raise AnalyticsUnavailable("Analytics historical import is still running")
        result = rows[0]
        result["generated_at"] = result["updated_at"]
        return result
