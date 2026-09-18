"""Compare artifacts from isolated parallel base and head load-test runners."""

import argparse
import json
import math
import os
import statistics
from pathlib import Path

ROUTES = (
    "Aggregated",
    "/v1/feed [latest]",
    "/v1/feed [page]",
    "/v1/articles/[id]",
    "/v1/feed [source]",
    "/v1/feed [topic]",
    "/v1/feed/options",
    "/v1/sources",
    "/v1/topics",
)


def classify(base, head):
    """Conservative repeated-sample decision, with absolute and relative noise floors."""
    if any(not math.isfinite(v) or v <= 0 for v in base + head):
        return "ERROR"
    if any(
        max(values) - min(values) > max(20, statistics.median(values) * 0.35)
        for values in (base, head)
    ):
        return "INCONCLUSIVE"
    b, h = statistics.median(base), statistics.median(head)
    slower = sum(y > b * 1.20 and y - b > 20 for y in head)
    faster = sum(y < b * 0.80 and b - y > 20 for y in head)
    if h > b * 1.20 and h - b > 20 and slower >= 2:
        return "REGRESSED"
    if h < b * 0.80 and b - h > 20 and faster >= 2:
        return "IMPROVED"
    return "NO MATERIAL CHANGE"


def report(runs, base_sha, head_sha):
    lines = [
        "# PR reader API performance",
        "",
        f"Base `{base_sha}` → PR `{head_sha}`",
        "",
        "Three independent runs per revision, each on a fresh GitHub-hosted ARM64 runner. "
        "16 users, 60 seconds/run, 1,000 synthetic articles, cache off, five PgBouncer slots.",
        "",
    ]
    problems = []
    samples = {"base": [], "head": []}
    for side in samples:
        for index in range(3):
            item = runs.get(f"{side}-{index}", {})
            if item.get("exit_code") != 0 or item.get("gate_failures"):
                problems.append(
                    f"{side} run {index + 1} failed or is missing; inspect its artifacts."
                )
            entries = {entry["name"]: entry for entry in item.get("entries", [])}
            for route in ROUTES:
                entry = entries.get(route, {})
                if entry.get("requests", 0) < 20 or entry.get("failures", 0):
                    problems.append(
                        f"{side} run {index + 1}: insufficient successful samples for {route}."
                    )
            samples[side].append(entries)
    lines += ["| Run | Requests | Failures | Exit |", "|---|---:|---:|---:|"]
    for side in samples:
        for index, entries in enumerate(samples[side]):
            total = entries.get("Aggregated", {})
            code = runs.get(f"{side}-{index}", {}).get("exit_code", "missing")
            lines.append(
                f"| {side} {index + 1} | {total.get('requests', 'missing')} | "
                f"{total.get('failures', 'missing')} | {code} |"
            )
    lines.append("")
    if problems:
        verdict = (
            "ERROR"
            if any(p.startswith("base") for p in problems)
            or any(item.get("invalid") for item in runs.values())
            else "REGRESSED"
        )
        lines += [
            f"**{verdict} — incomplete or failing workload.**",
            "",
            *[f"- {p}" for p in problems],
        ]
        lines += ["", "No latency improvement claim is made from failed or missing samples."]
        return verdict, "\n".join(lines) + "\n"
    lines += [
        "| Endpoint | Base p95 | PR p95 | Change | Base → PR p50 / p99 | "
        "Base → PR req/s | Result |",
        "|---|---:|---:|---:|---|---:|---|",
    ]
    results = []
    for route in ROUTES:

        def values(side, metric, route=route):
            return [entry[route][metric] for entry in samples[side]]

        base, head = values("base", "p95"), values("head", "p95")
        result = classify(base, head)
        results.append(result)
        b, h = statistics.median(base), statistics.median(head)
        if b <= 0 or not math.isfinite(b + h):
            return "ERROR", "\n".join(lines) + "\n**ERROR** — invalid latency samples.\n"

        def median(side, metric):
            return statistics.median(values(side, metric))

        lines.append(
            f"| {route} | {b:.0f} ms | {h:.0f} ms | {(h / b - 1) * 100:+.1f}% | "
            f"{median('base', 'p50'):.0f} → {median('head', 'p50'):.0f} / "
            f"{median('base', 'p99'):.0f} → {median('head', 'p99'):.0f} ms | "
            f"{median('base', 'rps'):.2f} → {median('head', 'rps'):.2f} | {result} |"
        )
    verdict = next(
        (v for v in ("ERROR", "REGRESSED", "INCONCLUSIVE", "IMPROVED") if v in results),
        "NO MATERIAL CHANGE",
    )
    lines[4:4] = [f"**{verdict}** — zero request failures in all six runs.", ""]
    lines += [
        "",
        "Values are medians of three runs. A p95 regression needs >20% **and** >20 ms "
        "increase in the medians and at least two head samples versus the base median. "
        "Improvements use the inverse "
        "threshold. A within-revision p95 range >35% of its median and >20 ms is inconclusive.",
        "",
        "Inconclusive latency comparisons are non-blocking: the check passes without claiming "
        "an improvement. Confirmed regressions and test/report errors fail the PR gate. "
        "Throughput is descriptive: "
        "paced users do not measure maximum capacity. This uncached public API test does "
        "not establish production health, browser performance or worker capacity. "
        "Separate runners can differ in hardware or host load; repetitions and noise checks "
        "reduce but cannot eliminate that uncertainty.",
    ]
    return verdict, "\n".join(lines) + "\n"


def collect(directory, base_sha, head_sha):
    runs = {}
    expected = {
        "users": 16,
        "spawn_rate": 4,
        "seconds": 60,
        "rows": 1000,
        "cache": "off",
        "pgbouncer_mode": "session",
        "server_slots": 5,
        "api_instances": 1,
        "requested_admission": 16,
        "requested_pool": "NullPool",
    }
    for side, sha in (("base", base_sha), ("head", head_sha)):
        for index in range(3):
            key = f"{side}-{index}"
            root = directory / key
            try:
                item = json.loads((root / "final.json").read_text())
                meta = json.loads((root / "metadata.json").read_text())
                code = int((root / "exit-code.txt").read_text())
                if meta["commit"] != sha or any(meta.get(k) != v for k, v in expected.items()):
                    raise ValueError("Mismatched revision or workload")
                if not isinstance(item["entries"], list) or not item["entries"]:
                    raise ValueError("Missing statistics")
                for entry in item["entries"]:
                    for field in ("requests", "failures", "p50", "p95", "p99", "rps"):
                        if not isinstance(entry[field], (int, float)) or not math.isfinite(
                            entry[field]
                        ):
                            raise ValueError("Invalid statistics")
                item["exit_code"] = code
                runs[key] = item
            except (OSError, ValueError, KeyError, TypeError):
                runs[key] = {"invalid": True, "exit_code": None}
    return runs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runs = collect(output, args.base_sha, args.head_sha)
    verdict, markdown = report(runs, args.base_sha, args.head_sha)
    (output / "comparison.json").write_text(
        json.dumps({"verdict": verdict, "runs": runs}, indent=2) + "\n"
    )
    (output / "summary.md").write_text(markdown)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(markdown)
    print(markdown)
    return int(verdict in {"REGRESSED", "ERROR"})


if __name__ == "__main__":
    raise SystemExit(main())
