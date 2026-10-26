"""Render bounded, comparable catalog scale evidence for the job and PR comment."""

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
GROUPS = {"blockers", "overview"}
SHARED_CHECKS = {
    "stable_reports_publish",
    "writer_commits_match_batches",
    "converges_after_writer_stops",
    "publication_generations_do_not_regress",
    "converged_cards_match_authoritative_lifecycle",
    "catalog_cursor_advances",
    "native_arm64_postgresql_17",
}


def mapping(value):
    return value if isinstance(value, dict) else {}


def number(value):
    try:
        return (
            value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
        )
    except OverflowError:
        return None


def integer(value):
    return value if type(value) is int and value >= 0 else None


def metric(value, unit="", *, digits=2):
    if number(value) is None:
        return "Unavailable"
    formatted = f"{value:,.{digits}f}" if digits else f"{value:,}"
    return f"{formatted} {unit}".strip()


def delta(before, after, unit="", *, digits=2):
    if number(before) is None or number(after) is None:
        return "Not comparable"
    change = after - before
    sign = "+" if change > 0 else "-" if change < 0 else ""
    formatted = metric(abs(change), unit, digits=digits)
    percent = f" ({change / before * 100:+.1f}%)" if before else ""
    return f"{sign}{formatted}{percent}"


def read_document(path):
    try:
        if not path.is_file() or path.is_symlink():
            return None, "result file missing"
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            return None, "result file too large"
        document = json.loads(path.read_text())
        if not isinstance(document, dict):
            return None, "invalid result document"
        return document, None
    except (OSError, UnicodeError, ValueError):
        return None, "result file unreadable or invalid JSON"


def summarize(document, error=None):
    if document is None:
        return {"phase": "unavailable", "complete": False, "error": error}
    phase = document.get("phase", "unknown")
    if not isinstance(phase, str) or re.fullmatch(r"[a-z_]{1,40}", phase) is None:
        phase = "unknown"
    values = {
        "phase": phase,
        "complete": document.get("complete") is True and phase == "complete",
        "error": error,
    }
    dataset = mapping(document.get("dataset"))
    for key in ("articles", "retained_article_cards"):
        values[key] = integer(dataset.get(key))
    workload = mapping(document.get("workload"))
    declared_groups = workload.get("report_groups")
    allowed_groups = (
        {"operations"}
        if declared_groups == ["operations"]
        else GROUPS
        if declared_groups is None
        else set()
    )
    values["reports_per_batch"] = len(allowed_groups)
    values["planned_batches"] = integer(workload.get("batches"))
    samples = document.get("continuous")
    if isinstance(samples, list):
        values["completed_batches"] = len(samples)
        results = []
        valid = bool(allowed_groups) and bool(samples) and len(samples) == values["planned_batches"]
        for sample in samples:
            batch_results = mapping(mapping(sample).get("reports")).get("result")
            if not isinstance(batch_results, list) or any(
                not isinstance(row, dict) for row in batch_results
            ):
                valid = False
                break
            groups = [row.get("group") for row in batch_results]
            if (
                len(groups) > len(allowed_groups)
                or any(group not in allowed_groups for group in groups if isinstance(group, str))
                or any(not isinstance(group, str) for group in groups)
                or len(set(groups)) != len(groups)
                or any(type(row.get("published")) is not bool for row in batch_results)
                or any(row.get("published") and row.get("error") for row in batch_results)
            ):
                valid = False
                break
            results.extend(batch_results)
        if valid:
            values.update(
                attempts=len(results),
                published=sum(row.get("published") is True for row in results),
                readiness_errors=sum(row.get("error") == "ProjectionNotReady" for row in results),
                suppressed=len(samples) * len(allowed_groups) - len(results),
            )
            snapshots = [row.get("snapshot") for row in results if row.get("published") is True]
            lags = [
                number(row.get("lag_seconds")) if isinstance(row, dict) else None
                for row in snapshots
            ]
            values["max_lag"] = (
                max(value for value in lags if value is not None)
                if lags and all(value is not None for value in lags)
                else None
            )
            catalog = [
                number(row.get("catalog_lag_seconds")) if isinstance(row, dict) else None
                for row in snapshots
            ]
            if catalog and all(value is not None for value in catalog):
                known = [value for value in catalog if value is not None]
                values["catalog_lag_min"], values["catalog_lag_max"] = min(known), max(known)
            values["catching_up"] = sum(
                isinstance(row, dict) and row.get("status") == "catching_up" for row in snapshots
            )
    gates = mapping(document.get("gates"))
    checks = gates.get("checks")
    observed = gates.get("observed_behavior")
    required_checks = SHARED_CHECKS | (
        {"publishes_every_requested_report", "reports_explicitly_catch_up"}
        if observed == "fixed"
        else {"recognizes_baseline_or_fixed_behavior"}
    )
    values["gates_passed"] = (
        gates.get("passed") is True
        and observed in ("baseline", "fixed")
        and isinstance(checks, dict)
        and required_checks <= checks.keys()
        and all(value is True for value in checks.values())
    )
    values["behavior"] = (
        {"baseline": "Starvation", "fixed": "Publishes during catch-up"}.get(
            observed, "Unconfirmed"
        )
        if isinstance(observed, str)
        else "Unconfirmed"
    )
    values["native"] = (
        mapping(document.get("qualification")).get("native_arm64_postgresql_17") is True
    )
    values["pending_events"] = integer(mapping(document.get("final_state")).get("pending_events"))
    parity = mapping(document.get("legacy_lifecycle_parity"))
    values["parity"] = parity.get("matches_legacy_lifecycle")
    for key in ("wall_seconds", "application_cpu_seconds"):
        values[f"convergence_{key}"] = number(mapping(document.get("convergence")).get(key))
    if isinstance(samples, list):
        for group, name in (("project", "projector_cpu"), ("reports", "report_cpu")):
            measurements = [
                number(mapping(mapping(sample).get(group)).get("application_cpu_seconds"))
                for sample in samples
            ]
            values[name] = (
                sum(measurements)
                if measurements and all(value is not None for value in measurements)
                else None
            )
    stats = mapping(document.get("database_activity_delta"))
    values["temp_mb"] = number(stats.get("temp_bytes"))
    if values["temp_mb"] is not None:
        values["temp_mb"] /= 1_000_000
    return values


def scenario_passed(side):
    metrics = (
        "convergence_wall_seconds",
        "convergence_application_cpu_seconds",
        "projector_cpu",
        "report_cpu",
        "temp_mb",
    )
    if not (
        side.get("gates_passed")
        and side.get("native")
        and side.get("planned_batches", 0)
        and side.get("completed_batches") == side.get("planned_batches")
        and integer(side.get("attempts")) is not None
        and all(number(side.get(key)) is not None for key in metrics)
        and side.get("pending_events") == 0
        and side.get("parity") is True
    ):
        return False
    if side.get("behavior") == "Starvation":
        return (
            side.get("published") == 0
            and side.get("readiness_errors") == side["attempts"]
            and side["attempts"] > 0
        )
    return (
        side.get("published")
        == side["planned_batches"] * side.get("reports_per_batch", len(GROUPS))
        and side["attempts"] == side["published"]
        and side.get("catching_up") == side["published"]
        and number(side.get("max_lag")) is not None
        and side["max_lag"] <= 60
    )


def render(before, after, *, job_result, base_sha, head_sha):
    missing = any(not side.get("complete") for side in (before, after))

    passed = (
        job_result == "success"
        and not missing
        and all(scenario_passed(side) for side in (before, after))
        and after.get("behavior") == "Publishes during catch-up"
        and before.get("articles") == after.get("articles")
        and number(after.get("articles")) is not None
        and after["articles"] >= 100000
        and before.get("retained_article_cards") == after.get("retained_article_cards")
        and number(after.get("retained_article_cards")) is not None
        and after["retained_article_cards"] >= 19679
        and before.get("planned_batches") == after.get("planned_batches")
    )
    status = "passed" if passed else "incomplete" if missing else "failed"
    label = {"passed": "PASS", "failed": "FAIL", "incomplete": "INCOMPLETE"}[status]
    qualification = (
        "Native ARM64 / PostgreSQL 17"
        if all(side.get("native") for side in (before, after))
        else "Native ARM64 / PostgreSQL 17 qualification unavailable"
    )
    fixture_context = (
        "cloned before either scenario."
        if not missing
        else "Scenario setup or measurement is incomplete; fixture cloning is unconfirmed."
    )
    lines = [
        "### Catalog refresh scale",
        "",
        f"**{label}** · {qualification} · Before `{base_sha[:12]}` → After `{head_sha[:12]}`",
        "",
        f"Fixture: **{metric(after.get('articles'), digits=0)} articles / "
        f"{metric(after.get('retained_article_cards'), digits=0)} retained cards**, "
        f"{fixture_context} "
        f"{metric(after.get('planned_batches'), digits=0)} catalog/ingestion commits expected.",
        "",
        "| Metric | Before | After | Delta (+/-) |",
        "| --- | ---: | ---: | ---: |",
    ]

    def row(title, key, unit="", digits=2):
        old, new = before.get(key), after.get(key)
        lines.append(
            f"| {title} | {metric(old, unit, digits=digits)} | "
            f"{metric(new, unit, digits=digits)} | {delta(old, new, unit, digits=digits)} |"
        )

    def slots(side):
        published, planned = side.get("published"), side.get("planned_batches")
        return (
            f"**{published}/{planned * side.get('reports_per_batch', 2)}**"
            if published is not None and planned is not None
            else "Unavailable"
        )

    old, new = before.get("published"), after.get("published")
    change = delta(old, new, digits=0)
    if (
        number(old) is not None
        and number(new) is not None
        and before.get("planned_batches") == after.get("planned_batches")
        and before.get("reports_per_batch") == after.get("reports_per_batch")
        and after.get("planned_batches")
    ):
        points = (new - old) / (after["planned_batches"] * after.get("reports_per_batch", 2)) * 100
        change = f"{new - old:+,} reports ({points:+.0f} pp)"
    lines.append(
        f"| **Published / requested reports** | {slots(before)} | {slots(after)} | **{change}** |"
    )
    row("ProjectionNotReady errors", "readiness_errors", digits=0)
    row("Report slots suppressed by cooldown", "suppressed", digits=0)
    row("Actual report attempts", "attempts", digits=0)
    row("Maximum operational lag", "max_lag", "s", digits=3)
    row("Pending events after convergence", "pending_events", digits=0)
    row("Recovery wall time", "convergence_wall_seconds", "s")
    cost_context = (
        "The candidate computes reports the starved baseline skips, so report CPU and temporary "
        "I/O include unequal completed work. "
        if before.get("behavior") == "Starvation"
        else "Compare completed report counts when interpreting report CPU and temporary I/O. "
    )
    lines.extend(
        [
            "",
            "**Cost of the measured work**",
            "",
            "| Metric | Before | After | Delta (+/-) |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    row("Projector application CPU (continuous phase)", "projector_cpu", "s")
    row("Report application CPU (continuous phase)", "report_cpu", "s")
    row("Recovery application CPU", "convergence_application_cpu_seconds", "s")
    row("Observed database temporary writes", "temp_mb", "MB")
    lines.extend(
        [
            "",
            "Positive deltas mean more; negative deltas mean less. More publications and fewer "
            "errors are improvements; higher CPU, I/O or recovery time are additional cost. "
            "These are single paired samples, not latency percentiles or a universal speedup.",
            "",
            cost_context + "Database counters also include captured-plan "
            "execution and recovery; they are not normalized per report.",
            "",
            f"Before: **{before.get('behavior', 'Unconfirmed')}**; phase `{before['phase']}`. "
            f"After: **{after.get('behavior', 'Unconfirmed')}**; phase `{after['phase']}`.",
        ]
    )
    if before.get("behavior") == "Starvation":
        lines.append(
            "A passing baseline gate means the original starvation was reproduced, "
            "not that the baseline was healthy. Unavailable lag is not zero: "
            "its report publication was rejected."
        )
    for title, side in (("Before", before), ("After", after)):
        if side.get("error"):
            lines.append(f"{title} evidence: {side['error']}.")
    if number(after.get("catalog_lag_min")) is not None:
        parity_label = "PASS" if after.get("parity") is True else "unavailable or failed"
        lines.append(
            "Published candidate reports remain explicitly `catching_up` while catalog age is "
            f"{after['catalog_lag_min']:.2f}–{after['catalog_lag_max']:.2f} s. "
            f"Lifecycle parity: {parity_label}."
        )
    if status != "passed":
        lines.append(
            "**This is not a passing scale check.** Missing or partial evidence is unavailable, "
            "not a zero or a successful measurement."
        )
    return status, "\n".join(lines) + "\n"


def render_current(after, *, job_result, head_sha):
    complete = after.get("complete")
    passed = (
        job_result == "success"
        and complete
        and scenario_passed(after)
        and after.get("behavior") == "Publishes during catch-up"
        and number(after.get("articles")) is not None
        and after["articles"] >= 100000
        and number(after.get("retained_article_cards")) is not None
        and after["retained_article_cards"] >= 19679
    )
    status = "passed" if passed else "incomplete" if not complete else "failed"
    label = {"passed": "PASS", "failed": "FAIL", "incomplete": "INCOMPLETE"}[status]
    lines = [
        "### Catalog refresh scale",
        "",
        f"**{label}** · PostgreSQL catalog + operations / native ARM64 · current `{head_sha[:12]}`",
        "",
        "Current architecture measurement; no paired baseline or performance delta is claimed.",
        "",
        "| Metric | Current |",
        "| --- | ---: |",
    ]
    for title, key, unit, digits in (
        ("Articles", "articles", "", 0),
        ("Retained cards", "retained_article_cards", "", 0),
        ("Published reports", "published", "", 0),
        ("Readiness errors", "readiness_errors", "", 0),
        ("Maximum operational lag", "max_lag", "s", 3),
        ("Pending events after convergence", "pending_events", "", 0),
        ("Recovery wall time", "convergence_wall_seconds", "s", 2),
        ("Report application CPU", "report_cpu", "s", 2),
        ("Observed PostgreSQL temporary writes", "temp_mb", "MB", 2),
    ):
        lines.append(f"| {title} | {metric(after.get(key), unit, digits=digits)} |")
    if status != "passed":
        lines.extend(
            ["", "**This is not a passing scale check.** Missing evidence is unavailable."]
        )
    return status, "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-result", choices=("success", "failure", "cancelled"), required=True)
    parser.add_argument("--candidate-only", action="store_true")
    args = parser.parse_args()
    run_id, attempt = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", "")
    base, head = os.environ.get("BASE_SHA", ""), os.environ.get("HEAD_SHA", "")
    if (
        not run_id.isdigit()
        or not attempt.isdigit()
        or int(attempt) < 1
        or any(re.fullmatch(r"[a-f0-9]{40}", sha) is None for sha in (base, head))
    ):
        parser.error("Explicit run ID, positive attempt, and exact base/head SHAs are required")
    directory = Path("reports")
    output = directory / "summary.md"
    metadata = directory / "summary.json"
    if directory.is_symlink() or output.is_symlink() or metadata.is_symlink():
        parser.error("Report artifacts must use ordinary files in the fixed reports directory")
    if args.candidate_only:
        document, error = read_document(directory / "candidate.json")
        status, markdown = render_current(
            summarize(document, error), job_result=args.job_result, head_sha=head
        )
    else:
        sides = []
        for name in ("baseline", "candidate"):
            document, error = read_document(directory / f"{name}.json")
            sides.append(summarize(document, error))
        status, markdown = render(*sides, job_result=args.job_result, base_sha=base, head_sha=head)
    directory.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown)
    metadata.write_text(
        json.dumps(
            {
                "schema": 1,
                "run_id": run_id,
                "run_attempt": int(attempt),
                "base_sha": base,
                "head_sha": head,
                "status": status,
                "summary_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
