"""Measure native ClickHouse reports on an explicitly owned local test database.

Requires DEVFEED_TEST_CLICKHOUSE_URL, DATABASE, USERNAME, and PASSWORD. The profiler
never connects to PostgreSQL, seeds data, or switches reporting implementations.
Prepare the disposable fixture with scale_fixture before measuring it.
"""

import argparse
import asyncio
import contextvars
import json
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

from devfeed_admin_api.analytics_reports import Reports, _gather
from devfeed_admin_api.overview_windows import parse_report_window
from devfeed_core.analytics import AnalyticsClient, AnalyticsUnavailable, _parameter
from devfeed_core.config import Settings

from scripts.scale_fixture import analytics_environment

GROUPS = ["publishing", "audience", "processing", "inference", "coverage"]
SECTION = contextvars.ContextVar("analytical_profile_section", default="metadata")


class QueryBudget:
    def __init__(self, client, limit, *, collect_costs=False):
        self.client, self.limit = client, limit
        self.queries = 0
        self.collect_costs = collect_costs
        self.tag = "devfeed-profile:" + uuid.uuid4().hex + ":"
        self.started_at = datetime.now(UTC) - timedelta(seconds=5)

    async def query(self, sql, parameters=None):
        if self.queries >= self.limit:
            raise RuntimeError("Analytical profiling query budget exhausted")
        self.queries += 1
        if self.collect_costs:
            # A SQL comment is lost when ClickHouse logs bound/rewritten queries.
            # Native log_comment survives that rewrite and carries no query data.
            response = await self.client._operation(
                "raw_query",
                sql,
                parameters={key: _parameter(value) for key, value in (parameters or {}).items()},
                fmt="JSONEachRow",
                settings=self.client._limits() | {"log_comment": self.tag + SECTION.get()},
            )
            return [json.loads(line) for line in response.splitlines() if line]
        return await self.client.query(sql, parameters)

    async def costs(self):
        await self.client.execute("SYSTEM FLUSH LOGS")
        return await self.client.query(
            "SELECT substring(log_comment, length({prefix:String}) + 1) AS section, "
            "count() AS queries, sum(query_duration_ms) AS total_query_duration_ms, "
            "sum(read_rows) AS read_rows, sum(read_bytes) AS read_bytes, "
            "max(memory_usage) AS peak_query_memory_bytes "
            "FROM system.query_log WHERE type = 'QueryFinish' "
            "AND event_date >= toDate({started:DateTime64(6, 'UTC')}) "
            "AND event_time_microseconds >= {started:DateTime64(6, 'UTC')} "
            "AND startsWith(log_comment, {prefix:String}) GROUP BY section ORDER BY section",
            {"prefix": self.tag, "started": self.started_at},
        )


async def profile(
    settings,
    *,
    groups,
    from_value,
    to_value,
    repeats,
    concurrency,
    max_queries,
    collect_costs=False,
):
    now = datetime.now(UTC)
    window = parse_report_window(from_value, to_value, now)
    assert window.start is not None and window.inclusive_end is not None
    async with AnalyticsClient(settings) as client:
        status = await client.status()
        if not status.get("ready"):
            raise AnalyticsUnavailable("Complete the disposable analytical backfill first")
        budget = QueryBudget(client, max_queries, collect_costs=collect_costs)
        semaphore = asyncio.Semaphore(concurrency)
        captured = datetime.fromisoformat(status["capture_started_at"])
        samples: dict[str, list[dict[str, float]]] = {group: [] for group in groups}

        async def measure(group):
            async with semaphore:
                started = time.perf_counter()
                token = SECTION.set(group)
                try:
                    result = await Reports(
                        budget, window, now, capture_started_at=captured
                    ).section(group)
                finally:
                    SECTION.reset(token)
                payload = result.model_dump_json().encode()
                samples[group].append(
                    {
                        "duration_ms": (time.perf_counter() - started) * 1000,
                        "response_bytes": len(payload),
                    }
                )

        started = time.perf_counter()
        # Each round has a bounded number of report tasks and completes before the next.
        for _ in range(repeats):
            await _gather(*(measure(group) for group in groups))
        result = {
            "complete": True,
            "backend": "clickhouse",
            "from": from_value,
            "to": to_value,
            "start": window.start.isoformat(),
            "end": window.inclusive_end.isoformat(),
            "days": window.days,
            "concurrency": concurrency,
            "queries": budget.queries,
            "metadata_queries": 3 if collect_costs else 1,
            "max_queries": max_queries,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "reports": {
                group: {
                    "samples": len(rows),
                    "median_ms": statistics.median(row["duration_ms"] for row in rows),
                    "max_ms": max(row["duration_ms"] for row in rows),
                    "max_response_bytes": max(row["response_bytes"] for row in rows),
                }
                for group, rows in samples.items()
            },
        }
        if collect_costs:
            costs = await budget.costs()
            result["query_costs"] = costs
            if sum(row["queries"] for row in costs) != budget.queries:
                raise RuntimeError("ClickHouse query log did not capture every profiling query")
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", action="append", choices=GROUPS)
    parser.add_argument("--from", dest="from_value", default="now-29d/d")
    parser.add_argument("--to", dest="to_value", default="now")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--max-queries", type=int, default=200)
    parser.add_argument(
        "--costs",
        action="store_true",
        help="Collect bounded query-log resource costs; requires SYSTEM FLUSH LOGS permission",
    )
    args = parser.parse_args()
    groups = list(dict.fromkeys(args.group or ("inference", "publishing", "processing")))
    if not (
        1 <= args.repeats <= 20
        and 1 <= args.concurrency <= 4
        and 1 <= args.max_queries <= 2000
        and len(groups) * args.repeats <= 200
    ):
        parser.error("Profiling arguments exceed bounded report/query limits")
    try:
        parse_report_window(args.from_value, args.to_value, datetime.now(UTC))
    except ValueError as error:
        parser.error(str(error))
    try:
        environment = analytics_environment()
    except (ValueError, KeyError) as error:
        parser.error(str(error))
    settings = Settings(
        _env_file=None,
        _env_prefix="DEVFEED_REPORT_PROFILE_",
        database_url="postgresql+psycopg://unused@database.invalid/analytics_profile_test",
        redis_url="redis://redis.invalid/15",
        clickhouse_url=environment["DEVFEED_CLICKHOUSE_URL"],
        clickhouse_database=environment["DEVFEED_CLICKHOUSE_DATABASE"],
        clickhouse_username=environment["DEVFEED_CLICKHOUSE_USERNAME"],
        clickhouse_password=environment["DEVFEED_CLICKHOUSE_PASSWORD"],
    )
    result = asyncio.run(
        profile(
            settings,
            groups=groups,
            from_value=args.from_value,
            to_value=args.to_value,
            repeats=args.repeats,
            concurrency=args.concurrency,
            max_queries=args.max_queries,
            collect_costs=args.costs,
        )
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
