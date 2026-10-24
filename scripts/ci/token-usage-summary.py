"""Render a token-report benchmark artifact as a concise, fail-closed PR comment."""

import argparse
import html
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCALES = (100000, 300000)
WINDOWS = (1, 7, 30)
MODES = ("custom", "generic")
KINDS = ("article_analysis", "topic_analysis", "research_verification")
FIELDS = ("total_rows", "one_day_rows", "seven_day_rows", "thirty_day_rows")
CHECKS = (
    "grouping_estimates_not_worse",
    "grouping_estimates_bounded",
    "aggregate_disk_usage_zero",
    "temporary_writes_zero",
    "no_sort_spills",
    "buffer_accesses_not_amplified",
    "runtime_within_noise_allowance",
)


def mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def sequence(value: Any) -> list:
    return value if isinstance(value, list) else []


def number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if math.isfinite(value) and value >= 0:
                return float(value)
        except OverflowError:
            pass
    return None


def display(value: Any, *, integer: bool = False) -> str:
    valid = number(value)
    return "n/a" if valid is None else f"{valid:,.0f}" if integer else f"{valid:,.2f}"


def improvement(before: Any, after: Any) -> str:
    first, second = number(before), number(after)
    if first is None or second is None or first == 0:
        return "n/a"
    return f"{(first - second) / first * 100:+.1f}%"


def safe_text(value: Any) -> str:
    value = html.escape(str(value), quote=True).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`|\[\]])", r"\\\1", value)[:200]


def samples(strategy: dict, field: str) -> float | None:
    values = [number(mapping(sample).get(field)) for sample in sequence(strategy.get("samples"))]
    if not values or any(value is None for value in values):
        return None
    return statistics.median(value for value in values if value is not None)


def peak_memory(strategy: dict) -> float | None:
    # Sorted aggregates do not report hash memory. Do not invent zero for them.
    values = [
        number(mapping(aggregate).get("Peak Memory Usage"))
        for aggregate in sequence(strategy.get("aggregates"))
        if "Peak Memory Usage" in mapping(aggregate)
    ]
    if not values or any(value is None for value in values):
        return None
    return max(value for value in values if value is not None)


def writer_samples(strategy: dict) -> list[float] | None:
    batches = sequence(strategy.get("ingestion"))
    values = [
        number(value) for batch in batches for value in sequence(mapping(batch).get("latency_ms"))
    ]
    if not batches or not values or any(value is None for value in values):
        return None
    for batch in batches:
        batch = mapping(batch)
        count, overlapping = (
            number(batch.get("insert_and_finish")),
            number(batch.get("completed_during_report")),
        )
        if (
            count != len(sequence(batch.get("latency_ms")))
            or overlapping is None
            or count is None
            or overlapping > count
        ):
            return None
    return [value for value in values if value is not None]


def p95(values: list[float] | None) -> float | None:
    return sorted(values)[math.ceil(len(values) * 0.95) - 1] if values else None


def table(headers: tuple[str, ...], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
    ]


def build_summary(
    data: Any, *, head_sha: str, run_url: str, outcome: str, error: str | None = None
) -> str:
    """Missing measurements are n/a; missing cases or gates can never produce PASS."""
    missing, failures = [], []
    if error:
        missing.append(error)
    if not isinstance(data, dict):
        missing.append("Profile root must be a JSON object.")
    data = mapping(data)
    scales = {}
    for scale in sequence(data.get("scales")):
        scale = mapping(scale)
        size = number(scale.get("articles"))
        if size not in SCALES or size in scales:
            missing.append("Unexpected or duplicate fixture scale.")
            continue
        scales[size] = scale
    reports = {}
    for size, scale in scales.items():
        for report in sequence(scale.get("reports")):
            report = mapping(report)
            days, mode = number(report.get("days")), report.get("plan_mode")
            report_key = (size, days, mode) if isinstance(mode, str) else None
            if days not in WINDOWS or mode not in MODES or report_key in reports:
                missing.append("Unexpected or duplicate report window/plan.")
                continue
            reports[report_key] = report

    passed_checks, equal, complete = 0, 0, 0
    runtime_rows, resource_rows, writer_rows = [], [], []
    for size in SCALES:
        for days in WINDOWS:
            for mode in MODES:
                context = f"{size:,} articles / {days}d / {mode}"
                report = reports.get((size, days, mode), {})
                strategies = mapping(report.get("strategies"))
                before, after = (mapping(strategies.get(name)) for name in ("original", "current"))
                first, second = number(before.get("median_ms")), number(after.get("median_ms"))
                runtime_rows.append(
                    [
                        f"{size:,}",
                        f"{days}d",
                        mode,
                        display(first),
                        display(second),
                        improvement(first, second),
                    ]
                )
                if first is None or second is None:
                    missing.append(f"{context}: runtime measurement missing or invalid.")
                else:
                    complete += 1
                equality = report.get("result_equality")
                if equality is True:
                    equal += 1
                elif equality is False:
                    failures.append(f"{context}: token accounting differs.")
                else:
                    missing.append(f"{context}: accounting comparison missing.")
                checks = mapping(report.get("checks"))
                for name in CHECKS:
                    if checks.get(name) is True:
                        passed_checks += 1
                    elif checks.get(name) is False:
                        failures.append(f"{context}: {name}.")
                    else:
                        missing.append(f"{context}: {name} missing or invalid.")
                for name, strategy in (("before", before), ("after", after)):
                    sample_values = sequence(strategy.get("samples"))
                    if any(
                        samples(strategy, field) is None
                        for field in (
                            "elapsed_ms",
                            "application_cpu_ms",
                            "database_container_cpu_ms",
                        )
                    ):
                        missing.append(f"{context}: {name} CPU/runtime samples missing or invalid.")
                    aggregates = sequence(strategy.get("aggregates"))
                    buffers = mapping(strategy.get("buffers"))
                    if (
                        not aggregates
                        or any(
                            number(mapping(group).get("Plan Rows")) is None for group in aggregates
                        )
                        or any(
                            "Peak Memory Usage" in mapping(group)
                            and number(group["Peak Memory Usage"]) is None
                            for group in aggregates
                        )
                        or any(
                            number(buffers.get(field)) is None
                            for field in ("Temp Read Blocks", "Temp Written Blocks")
                        )
                    ):
                        missing.append(f"{context}: {name} plan measurements missing or invalid.")
                    if (
                        len(sequence(strategy.get("ingestion"))) != len(sample_values)
                        or writer_samples(strategy) is None
                    ):
                        missing.append(f"{context}: {name} writer measurements missing or invalid.")
                if days == 30:
                    for metric, getter in (
                        (
                            "DB container CPU (ms)",
                            lambda strategy: samples(strategy, "database_container_cpu_ms"),
                        ),
                        ("App CPU (ms)", lambda strategy: samples(strategy, "application_cpu_ms")),
                        (
                            "Temp writes (blocks)",
                            lambda strategy: mapping(strategy.get("buffers")).get(
                                "Temp Written Blocks"
                            ),
                        ),
                        ("Largest hash aggregate (KiB)", peak_memory),
                    ):
                        first, second = getter(before), getter(after)
                        resource_rows.append(
                            [
                                f"{size:,}",
                                mode,
                                metric,
                                display(first),
                                display(second),
                                improvement(first, second),
                            ]
                        )
                old_writes, new_writes = writer_samples(before), writer_samples(after)
                old_p95, new_p95 = p95(old_writes), p95(new_writes)
                if (
                    old_p95 is not None
                    and new_p95 is not None
                    and old_writes is not None
                    and new_writes is not None
                    and new_p95 > old_p95 * 1.2
                ):
                    writer_rows.append(
                        [
                            f"{size:,} / {days}d / {mode}",
                            display(old_p95),
                            display(new_p95),
                            improvement(old_p95, new_p95),
                            f"{len(old_writes)} / {len(new_writes)}",
                        ]
                    )

    baseline = mapping(data.get("production_volume"))
    coverage_rows, volume_passed = [], 0
    volume_fields = [("Articles", "articles", None)] + [
        (
            f"{kind.replace('_', ' ')} / {field.replace('_rows', '').replace('_', ' ')}",
            f"{kind}.{field}",
            (kind, field),
        )
        for kind in KINDS
        for field in FIELDS
    ]
    for label, key, history in volume_fields:
        production = (
            baseline.get("articles")
            if history is None
            else mapping(mapping(baseline.get("histories")).get(history[0])).get(history[1])
        )
        row = [label, display(production, integer=True)]
        for size in SCALES:
            scale = scales.get(size, {})
            actual = (
                scale.get("articles")
                if history is None
                else mapping(mapping(scale.get("histories")).get(history[0])).get(history[1])
            )
            first, second = number(production), number(actual)
            recorded = mapping(scale.get("production_volume_checks")).get(key)
            if first is None or second is None or not isinstance(recorded, bool):
                missing.append(f"{size:,} articles: production volume evidence missing for {key}.")
                status = "n/a"
            elif second < first or recorded is False:
                failures.append(f"{size:,} articles: production volume not covered for {key}.")
                status = "FAIL"
            else:
                volume_passed += 1
                status = "PASS"
            row.append(f"{display(actual, integer=True)} ({status})")
        coverage_rows.append(row)

    if outcome == "failure":
        failures.append(
            "Benchmark measurement step failed; "
            "partial passing measurements do not establish success."
        )
    elif outcome != "success":
        missing.append(f"Benchmark measurement step outcome: {safe_text(outcome)}.")
    verdict = "FAIL" if failures else "INCOMPLETE" if missing else "PASS"
    reason = {
        "PASS": "all 12 comparisons, accounting checks and production-volume gates passed",
        "FAIL": "benchmark failed; review the failed checks below",
        "INCOMPLETE": "benchmark evidence is incomplete; no performance pass can be claimed",
    }[verdict]
    parsed_url = urlparse(run_url)
    run = (
        f"[Benchmark run]({run_url})"
        if parsed_url.scheme == "https"
        and parsed_url.netloc == "github.com"
        and re.fullmatch(r"/[A-Za-z0-9_./-]+", parsed_url.path)
        and not parsed_url.query
        and not parsed_url.fragment
        else "Benchmark run link unavailable"
    )
    architecture = data.get("client_architecture")
    architecture = architecture if architecture in ("aarch64", "arm64", "x86_64") else "unknown"
    lines = [
        f"**{verdict} — Token usage benchmark:** {reason}.",
        "",
        f"Compared head: `{safe_text(head_sha)}` · {run} · Architecture: `{architecture}`.",
        "",
        "Before = frozen pre-fix SQL; after = application SQL at the compared head. "
        "Improvement = `(before − after) / before × 100`: **+ means better, − means worse**; "
        "`n/a` means missing evidence or a zero baseline. "
        "Lower is better for every performance metric.",
        "",
        "**Query runtime — median milliseconds**",
        "",
        *table(
            ("Articles", "Window", "Plan", "Before (ms)", "After (ms)", "Improvement (+/−)"),
            runtime_rows,
        ),
        "",
        "**Validation**",
        "",
        *table(
            ("Check", "Passed / expected"),
            [
                ["Runtime comparisons", f"{complete} / 12"],
                ["Identical token accounting", f"{equal} / 12"],
                ["Runtime, plan and spill gates", f"{passed_checks} / 84"],
                ["Production-volume gates", f"{volume_passed} / 26"],
                ["Measurement step", safe_text(outcome)],
            ],
        ),
        "",
        "Runtime gate allows up to 20% or 20 ms noise; PASS does not mean every case got faster. "
        "Writer latency is observational and has no pass/fail gate. "
        "Its samples include writer startup and reports may finish "
        "before a subsequent write completes.",
    ]
    if writer_rows:
        lines.extend(
            [
                "",
                "**Writer latency caveats — p95 regressions over 20%**",
                "",
                *table(
                    (
                        "Fixture / window / plan",
                        "Before p95 (ms)",
                        "After p95 (ms)",
                        "Improvement (+/−)",
                        "Samples before / after",
                    ),
                    writer_rows,
                ),
                "",
                "p95 uses the nearest-rank sample. Small sample counts and startup outliers "
                "prevent a sustained-load latency conclusion.",
            ]
        )
    for title, entries in (("Failed checks", failures), ("Missing or invalid evidence", missing)):
        if entries:
            lines.extend(["", f"**{title}**", "", *(f"- {entry}" for entry in entries[:20])])
            if len(entries) > 20:
                lines.append(
                    f"- {len(entries) - 20} more; inspect the JSON artifact in the benchmark run."
                )
    lines.extend(
        [
            "",
            "<details>",
            "<summary>30-day CPU, temporary writes and hash memory</summary>",
            "",
            *table(
                ("Articles", "Plan", "Metric", "Before", "After", "Improvement (+/−)"),
                resource_rows,
            ),
            "",
            "DB CPU is the container CPU median, including concurrent ingestion; "
            "elapsed time is measured separately. "
            "Hash memory is the largest reported aggregate node, not total process memory. "
            "Temporary writes are PostgreSQL blocks from EXPLAIN.",
            "",
            "</details>",
            "",
            "<details>",
            "<summary>Production-volume coverage: retained history and 1/7/30-day counts</summary>",
            "",
            *table(
                ("Fixture", "Production snapshot", "100,000 articles", "300,000 articles"),
                coverage_rows,
            ),
            "",
            *(
                [f"Production snapshot observed at: `{safe_text(baseline['observed_at'])}`.", ""]
                if baseline.get("observed_at")
                else []
            ),
            "Production counts are the captured snapshot, not a live production benchmark.",
            "",
            "</details>",
        ]
    )
    return "\n".join(lines) + "\n"


def render(profile: Path, *, head_sha: str, run_url: str, outcome: str) -> str:
    try:
        data = json.loads(profile.read_text())
        error = None
    except (OSError, UnicodeError, ValueError):
        data, error = {}, "Profile artifact is missing, unreadable or malformed."
    return build_summary(data, head_sha=head_sha, run_url=run_url, outcome=outcome, error=error)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument(
        "--outcome", choices=("success", "failure", "skipped", "cancelled"), required=True
    )
    args = parser.parse_args()
    summary = render(
        args.profile, head_sha=args.head_sha, run_url=args.run_url, outcome=args.outcome
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(summary)
    if not summary.startswith("**PASS"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
