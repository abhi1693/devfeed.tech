"""Read-only inference usage report with explicitly estimated standard API prices."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

from devfeed_core.analytics import AnalyticsClient

# USD / million tokens, verified 2026-09-24. This is not Codex subscription billing.
# input, cached input, cache write, output (reasoning is already included in output).
PRICES = {
    "gpt-6-sol": ("2", "0.2", "2.5", "10"),
    "gpt-6-luna": ("0.1", "0.01", "0.125", "0.5"),
    "gpt-5.6-terra": ("2", "0.2", "2.5", "12"),
    "gpt-5.6-luna": ("0.2", "0.02", "0.25", "1.2"),
    "gpt-5.6-sol": ("4", "0.4", "5", "20"),
    "gpt-5.5": ("5", "0.5", "5", "30"),
}


def estimated_cost(model: str, counts: dict) -> Decimal | None:
    if model not in PRICES:
        return None
    rates = [Decimal(value) for value in PRICES[model]]
    inputs = int(counts.get("inputTokens", 0))
    cached = int(counts.get("cachedInputTokens", 0))
    writes = int(counts.get("cacheWriteInputTokens", 0))
    output = int(counts.get("outputTokens", 0))
    if min(inputs, cached, writes, output) < 0 or cached + writes > inputs:
        return None
    return (
        (inputs - cached - writes) * rates[0]
        + cached * rates[1]
        + writes * rates[2]
        + output * rates[3]
    ) / 1_000_000 + Decimal(int(counts.get("web_searches", 0))) / 100


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("Include a timezone, for example 2026-09-13T00:00:00Z")
    return parsed.astimezone(UTC)


async def usage_rows(client, since, until):
    """Group durable invocation facts from ClickHouse without reading PostgreSQL."""
    rows = await client.query(
        "SELECT task AS operation,if(model_is_null=1,NULL,if(model_is_empty=1,'',model)) AS model,"
        "if(effort_is_null=1,NULL,if(effort_is_empty=1,'',effort)) AS reasoning_effort,reason,"
        "count() AS calls,countIf(status='running') AS unfinished,"
        "countIf(has_input_tokens=0) AS unmetered,"
        "sum(toUInt128(web_searches)) AS web_searches,"
        "sum(toUInt128(input_tokens)) AS `inputTokens`,"
        "sum(toUInt128(reported_cached_input_tokens)) AS `cachedInputTokens`,"
        "sum(toUInt128(cache_write_input_tokens)) AS `cacheWriteInputTokens`,"
        "sum(toUInt128(output_tokens)) AS `outputTokens`,"
        "sum(toUInt128(reasoning_tokens)) AS `reasoningOutputTokens` "
        "FROM analytics_inference_events FINAL "
        "WHERE started_at >= {since:DateTime64(6,'UTC')} "
        "AND started_at < {until:DateTime64(6,'UTC')} "
        "GROUP BY task,model,model_is_null,model_is_empty,effort,"
        "effort_is_null,effort_is_empty,reason "
        "ORDER BY operation,model,reasoning_effort,reason "
        "SETTINGS optimize_move_to_prewhere_if_final=0,prefer_column_name_to_alias=1",
        {"since": since, "until": until},
    )
    for row in rows:
        for field in (
            "calls",
            "unfinished",
            "unmetered",
            "web_searches",
            "inputTokens",
            "cachedInputTokens",
            "cacheWriteInputTokens",
            "outputTokens",
            "reasoningOutputTokens",
        ):
            row[field] = int(row[field])
    return rows


async def read_usage(since, until):
    async with AnalyticsClient() as client:
        return await usage_rows(client, since, until)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=timestamp, required=True)
    parser.add_argument("--until", type=timestamp, default=datetime.now(UTC))
    args = parser.parse_args()
    if args.since >= args.until:
        parser.error("--since must be before --until")
    rows = asyncio.run(read_usage(args.since, args.until))
    for row in rows:
        cost = estimated_cost(row["model"], row)
        row["api_equivalent_usd"] = str(cost) if cost is not None else None
    print(
        json.dumps(
            {
                "since": args.since.isoformat(),
                "until": args.until.isoformat(),
                "pricing_checked": "2026-09-13",
                "pricing_source": "https://developers.openai.com/api/docs/pricing",
                "assumptions": (
                    "Standard short-context rates and observed cache hits; web search $0.01/call. "
                    "Not a bill. Unfinished/unmetered calls and pre-migration usage "
                    "are incomplete. "
                    "No monthly extrapolation."
                ),
                "groups": rows,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
