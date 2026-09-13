"""Read-only inference usage report with explicitly estimated standard API prices."""

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal

from devfeed_core.db import get_engine
from sqlalchemy import text

# USD / million tokens, verified 2026-09-13. This is not Codex subscription billing.
# input, cached input, cache write, output (reasoning is already included in output).
PRICES = {
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=timestamp, required=True)
    parser.add_argument("--until", type=timestamp, default=datetime.now(UTC))
    args = parser.parse_args()
    if args.since >= args.until:
        parser.error("--since must be before --until")
    counters = (
        "inputTokens",
        "cachedInputTokens",
        "cacheWriteInputTokens",
        "outputTokens",
        "reasoningOutputTokens",
    )
    sums = ", ".join(
        f"coalesce(sum((tokens->>'{name}')::bigint),0) as \"{name}\"" for name in counters
    )
    with get_engine().connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout='15s'"))
        rows = [
            dict(row)
            for row in connection.execute(
                text(f"""
            SELECT operation, model, reasoning_effort, reason,
                   count(*) calls, count(*) FILTER (WHERE status='running') unfinished,
                   count(*) FILTER (WHERE NOT (tokens ? 'inputTokens')) unmetered,
                   sum(web_searches) web_searches, {sums}
            FROM inference_calls
            WHERE started_at >= :since AND started_at < :until
            GROUP BY operation, model, reasoning_effort, reason
            ORDER BY operation, model, reasoning_effort, reason
        """),
                {"since": args.since, "until": args.until},
            ).mappings()
        ]
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
