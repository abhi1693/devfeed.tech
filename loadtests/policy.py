"""Pure validation helpers; importing tests must not monkey-patch pytest with gevent."""

import ipaddress
import math
from urllib.parse import urlsplit


def validate_target(host, allow_remote=False):
    target = urlsplit(host or "")
    if (
        target.scheme not in {"http", "https"}
        or not target.hostname
        or target.username
        or target.password
        or target.query
        or target.fragment
        or target.path not in {"", "/"}
    ):
        raise ValueError("Provide an HTTP(S) origin without credentials, path, query or fragment")
    try:
        local = ipaddress.ip_address(target.hostname).is_loopback
    except ValueError:
        local = target.hostname == "localhost"
    if not local and not allow_remote:
        raise ValueError("Remote load generation requires --allow-remote-target")


def valid_payload(kind, data):
    if kind == "feed":
        return (
            isinstance(data, dict)
            and isinstance(data.get("items"), list)
            and all(valid_payload("article", item) for item in data["items"])
            and "next_cursor" in data
            and (data["next_cursor"] is None or isinstance(data["next_cursor"], str))
        )
    if kind == "article":
        return isinstance(data, dict) and all(
            isinstance(data.get(key), str) and bool(data[key]) for key in ("id", "slug", "title")
        )
    if kind in {"topics", "sources"}:
        return isinstance(data, list) and all(
            isinstance(item, dict)
            and all(isinstance(item.get(key), str) and bool(item[key]) for key in ("id", "slug"))
            for item in data
        )
    if kind == "options":
        return isinstance(data, dict) and all(
            isinstance(data.get(key), list) for key in ("content_types", "sources")
        )
    if kind == "search":
        return (
            isinstance(data, dict)
            and isinstance(data.get("sections"), dict)
            and all(
                isinstance(data["sections"].get(key), dict)
                and isinstance(data["sections"][key].get("items"), list)
                for key in ("articles", "topics", "sources", "tags")
            )
        )
    raise ValueError("Unknown response kind")


def gate_failures(total, entries, *, minimum, max_failure_ratio, max_p95_ms, required):
    failures = []
    if total.num_requests < minimum:
        failures.append(f"Only {total.num_requests} requests; need {minimum}")
    if total.fail_ratio > max_failure_ratio:
        failures.append(f"Failure ratio {total.fail_ratio:.4f} exceeds {max_failure_ratio}")
    completed = {entry.name for entry in entries if entry.num_requests > entry.num_failures}
    for name in sorted(set(required) - completed):
        failures.append(f"No successful samples for {name}")
    for entry in [total, *entries]:
        if entry.num_requests < 5:
            continue
        p95 = entry.get_response_time_percentile(0.95)
        if p95 is None or not math.isfinite(p95) or p95 > max_p95_ms:
            failures.append(f"{entry.name}: p95 {p95} exceeds {max_p95_ms}ms")
    return failures
