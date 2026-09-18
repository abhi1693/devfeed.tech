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


LABELS = {
    "Aggregated": "All requests",
    "/v1/feed [latest]": "Latest feed",
    "/v1/feed [page]": "Feed pagination",
    "/v1/articles/[id]": "Article detail",
    "/v1/feed [source]": "Source feed",
    "/v1/feed [topic]": "Topic feed",
    "/v1/feed/options": "Feed filters",
    "/v1/sources": "Sources",
    "/v1/topics": "Topics",
}
RESULT_LABELS = {
    "REGRESSED": "**Regressed**",
    "IMPROVED": "**Improved**",
    "INCONCLUSIVE": "Inconclusive",
    "NO MATERIAL CHANGE": "Within threshold",
    "ERROR": "**Error**",
}


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
    run_lines = ["| Run | Requests | Failures | Exit code |", "|---|---:|---:|---:|"]
    for side in samples:
        for index, entries in enumerate(samples[side]):
            total = entries.get("Aggregated", {})
            code = runs.get(f"{side}-{index}", {}).get("exit_code", "missing")
            run_lines.append(
                f"| {'Base' if side == 'base' else 'PR'} {index + 1} | "
                f"{total.get('requests', 'missing')} | "
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
        lines += ["", *run_lines]
        return verdict, "\n".join(lines) + "\n"
    lines += [
        "## Latency comparison",
        "",
        "All latency values are in **milliseconds**; lower is better. "
        "Values are medians of three runs.",
        "",
        "| Route | Base p95 | PR p95 | Change | Result |",
        "|---|---:|---:|---:|---|",
    ]
    detail_lines = [
        "| Endpoint | p50 | p99 | Requests/s |",
        "|---|---:|---:|---:|",
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
            f"| {LABELS[route]} | {b:.0f} | {h:.0f} | "
            f"{h - b:+.0f} ({(h / b - 1) * 100:+.1f}%) | {RESULT_LABELS[result]} |"
        )
        detail_lines.append(
            f"| `{route}` | {median('base', 'p50'):.0f} → {median('head', 'p50'):.0f} | "
            f"{median('base', 'p99'):.0f} → {median('head', 'p99'):.0f} | "
            f"{median('base', 'rps'):.2f} → {median('head', 'rps'):.2f} |"
        )
    verdict = next(
        (v for v in ("ERROR", "REGRESSED", "INCONCLUSIVE", "IMPROVED") if v in results),
        "NO MATERIAL CHANGE",
    )
    counts = [
        f"{results[1:].count(value)} {label}"
        for value, label in (
            ("REGRESSED", "regressed"),
            ("IMPROVED", "improved"),
            ("INCONCLUSIVE", "inconclusive"),
            ("NO MATERIAL CHANGE", "within threshold"),
        )
        if value in results[1:]
    ]
    lines[2:2] = [
        f"**Result: {verdict}**",
        "",
        " · ".join(counts) + ". Zero request failures across all six runs.",
        "",
    ]
    lines += [
        "",
        "<details>",
        "<summary>Additional latency and throughput metrics</summary>",
        "",
        "Each cell shows **base → PR**. Latencies are in milliseconds.",
        "",
        *detail_lines,
        "",
        "</details>",
        "",
        "<details>",
        "<summary>Individual runs</summary>",
        "",
        *run_lines,
        "",
        "</details>",
        "",
        "<details>",
        "<summary>How to interpret this report</summary>",
    ]
    lines += [
        "",
        "**Regressed / Improved:** a p95 regression needs >20% **and** >20 ms "
        "increase in the medians and at least two head samples versus the base median. "
        "Improvements use the inverse "
        "threshold. **Within threshold** means the change did not meet both thresholds.",
        "",
        "**Inconclusive:** a within-revision p95 range >35% of its median and >20 ms "
        "is too noisy for a confident latency verdict.",
        "",
        "Inconclusive latency comparisons are non-blocking: the check passes without claiming "
        "an improvement. Confirmed regressions and test/report errors fail the PR gate. "
        "Throughput is descriptive: "
        "paced users do not measure maximum capacity. This uncached public API test does "
        "not establish production health, browser performance or worker capacity. "
        "Separate runners can differ in hardware or host load; repetitions and noise checks "
        "reduce but cannot eliminate that uncertainty.",
        "",
        "</details>",
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
