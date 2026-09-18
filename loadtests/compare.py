"""Run the same harness against two isolated application environments and report changes."""

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
    """Conservative repeated-pair decision, with absolute and relative noise floors."""
    if any(not math.isfinite(v) or v <= 0 for v in base + head):
        return "INCONCLUSIVE"
    if any(
        max(values) - min(values) > max(20, statistics.median(values) * 0.35)
        for values in (base, head)
    ):
        return "INCONCLUSIVE"
    b, h = statistics.median(base), statistics.median(head)
    slower = sum(y > x * 1.20 and y - x > 20 for x, y in zip(base, head, strict=True))
    faster = sum(y < x * 0.80 and x - y > 20 for x, y in zip(base, head, strict=True))
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
        "Three paired runs, alternating order, same runner and harness. "
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
        verdict = "INCONCLUSIVE" if any(p.startswith("base") for p in problems) else "REGRESSED"
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
            return "INCONCLUSIVE", "\n".join(lines) + "\nInvalid latency samples.\n"

        def median(side, metric):
            return statistics.median(values(side, metric))

        lines.append(
            f"| {route} | {b:.0f} ms | {h:.0f} ms | {(h / b - 1) * 100:+.1f}% | "
            f"{median('base', 'p50'):.0f} → {median('head', 'p50'):.0f} / "
            f"{median('base', 'p99'):.0f} → {median('head', 'p99'):.0f} ms | "
            f"{median('base', 'rps'):.2f} → {median('head', 'rps'):.2f} | {result} |"
        )
    verdict = next(
        (v for v in ("REGRESSED", "INCONCLUSIVE", "IMPROVED") if v in results), "NO MATERIAL CHANGE"
    )
    lines[4:4] = [f"**{verdict}** — zero request failures in all six runs.", ""]
    lines += [
        "",
        "Values are medians of three runs. A p95 regression needs >20% **and** >20 ms "
        "increase in the medians and at least two paired runs. Improvements use the inverse "
        "threshold. A within-revision p95 range >35% of its median and >20 ms is inconclusive.",
        "",
        "Regressed and inconclusive results fail the PR gate. Throughput is descriptive: "
        "paced users do not measure maximum capacity. This uncached public API test does "
        "not establish production health, browser performance or worker capacity.",
    ]
    return verdict, "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--head", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    roots = {side: getattr(args, side).resolve() for side in ("base", "head")}
    shas = {
        side: subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        for side, root in roots.items()
    }
    runs = {}
    verdict = "INCONCLUSIVE"
    try:
        for index in range(3):
            for side in ("base", "head") if index % 2 == 0 else ("head", "base"):
                key = f"{side}-{index}"
                target = output / key
                target.mkdir()
                with (target / "runner.log").open("w") as log:
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(ROOT / "scripts/load-test.py"),
                            "--app-root",
                            str(roots[side]),
                            "--app-python",
                            str(roots[side] / ".venv/bin/python"),
                            "--users",
                            "16",
                            "--spawn-rate",
                            "4",
                            "--seconds",
                            "60",
                            "--rows",
                            "1000",
                            "--output",
                            str(target),
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=360,
                    )
                final = target / "final.json"
                runs[key] = json.loads(final.read_text()) if final.exists() else {}
                runs[key]["exit_code"] = result.returncode
                print(f"{key}: exit {result.returncode}", flush=True)
    finally:
        verdict, markdown = report(runs, shas["base"], shas["head"])
        (output / "comparison.json").write_text(
            json.dumps({"verdict": verdict, "runs": runs}, indent=2) + "\n"
        )
        (output / "summary.md").write_text(markdown)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
                summary.write(markdown)
        print(markdown)
    return int(verdict in {"REGRESSED", "INCONCLUSIVE"})


if __name__ == "__main__":
    raise SystemExit(main())
