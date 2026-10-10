"""Deterministic 90-day workload; run `uv run python scripts/testing/adaptive_polling.py`.

120 sources, four workers, one-minute jobs/ticks, scheduler batches of 20.
Every 97th request returns a one-hour rate-limit cooldown. No network is used.
"""

import json
from datetime import UTC, datetime, timedelta

from devfeed_core.polling import decide

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
DAY = 86400


def publications(kind, second):
    if kind == "hourly":
        return second // 3600
    if kind == "weekly":
        return second // (7 * DAY)
    return max(0, second - 15 * DAY) // 3600


def publication_time(kind, index):
    return index * (7 * DAY if kind == "weekly" else 3600) + (15 * DAY if kind == "resumed" else 0)


def compare(adaptive, days=90):
    sources = [
        {"kind": kind, "due": index % 60, "state": {}, "seen": 0, "active": False, "requests": 0}
        for kind in ["hourly", "weekly", "resumed"]
        for index in range(40)
    ]
    queued, running = [], []
    groups = {
        kind: {"requests": 0, "discoveries": 0, "latency_seconds": 0}
        for kind in ["hourly", "weekly", "resumed"]
    }
    max_depth = max_delay = total_delay = failures = requests = 0
    for now in range(0, days * DAY + 1, 60):
        for source, _queued_at in running:
            source["active"] = False
            source["requests"] += 1
            requests += 1
            group = groups[source["kind"]]
            group["requests"] += 1
            if requests % 97 == 0:
                failures += 1
                source["due"] = now + 3600
                continue
            count = publications(source["kind"], now)
            arrivals = count - source["seen"]
            for index in range(source["seen"] + 1, count + 1):
                group["latency_seconds"] += now - publication_time(source["kind"], index)
            group["discoveries"] += arrivals
            source["seen"] = count
            if adaptive:
                source["state"] = decide(
                    source["state"], now=EPOCH + timedelta(seconds=now), arrivals=arrivals
                )
            interval = source["state"].get("interval", 43200) if adaptive else 43200
            source["due"] = now + interval
        due = sorted(
            (s for s in sources if not s["active"] and s["due"] <= now), key=lambda s: s["due"]
        )
        for source in due[:20]:
            source["active"] = True
            queued.append((source, now))
        max_depth = max(max_depth, len(queued))
        running, queued = queued[:4], queued[4:]
        for _source, queued_at in running:
            delay = now - queued_at
            max_delay = max(max_delay, delay)
            total_delay += delay
    for group in groups.values():
        group["mean_discovery_seconds"] = round(
            group.pop("latency_seconds") / max(1, group["discoveries"]), 1
        )
    return {
        "groups": groups,
        "requests": requests,
        "rate_limit_errors": failures,
        "error_rate": round(failures / requests, 4),
        "max_queue_depth": max_depth,
        "max_queue_delay_seconds": max_delay,
        "mean_queue_delay_seconds": round(total_delay / requests, 1),
        "minimum_fetches_per_source": min(s["requests"] for s in sources),
    }


if __name__ == "__main__":
    print(json.dumps({"fixed": compare(False), "adaptive": compare(True)}, indent=2))
