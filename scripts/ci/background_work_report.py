"""Compare completed native sustained workloads without hiding shifted costs."""

import argparse
import json
import math
from pathlib import Path


def absolute(candidate, *, candidate_sha=None):
    failures, rows = [], []
    if not candidate.get("complete") or candidate.get("qualification") != "native-scale":
        failures.append("Candidate is incomplete or is not a native scale run")
    if not candidate.get("gates", {}).get("passed"):
        failures.append("Candidate exceeded an absolute resource or behavior budget")
    if candidate.get("worktree_dirty") or candidate.get("source_worktree_changes"):
        failures.append("Candidate source contains uncommitted changes")
    if not candidate.get("harness_sha256"):
        failures.append("Candidate measurement harness is not identified")
    if candidate_sha is not None and candidate.get("revision") != candidate_sha:
        failures.append("Candidate does not match the requested commit")
    phases = {phase["name"]: phase for phase in candidate.get("phases", [])}
    for name in ("active", "idle", "idle-expired", "recovery", "drain"):
        if name not in phases:
            failures.append(name + " phase is missing")
            continue
        phase = phases[name]
        seconds = phase["seconds"]
        if not math.isfinite(seconds) or seconds <= 0:
            failures.append(name + " has no measured duration")
            continue
        costs = {
            field: phase["cost"].get(field)
            for field in ("total_exec_time", "shared_blks_hit", "shared_blks_read", "wal_bytes")
        }
        costs["python_cpu_seconds"] = (
            sum(row["cpu_seconds"] for row in phase["processes"].values())
            if phase.get("processes")
            else None
        )
        costs["global_wal_bytes"] = phase.get("database_cost", {}).get("global_wal_position_bytes")
        for field, value in costs.items():
            if value is None or not math.isfinite(value) or value < 0:
                failures.append(name + ": missing or invalid " + field)
            else:
                rows.append(
                    (name, field, 0, value * (1 if name == "drain" else 60 / seconds), None)
                )
    return {"passed": not failures, "failures": failures, "rows": rows}


def compare(baseline, candidate, *, baseline_sha=None, candidate_sha=None):
    failures, rows = absolute(candidate, candidate_sha=candidate_sha)["failures"], []
    if not baseline.get("complete") or baseline.get("qualification") != "native-scale":
        failures.append("Baseline is incomplete or is not a native scale run")
    baseline_changes = baseline.get("source_worktree_changes", [])
    if any(path != "scripts/profile_background_work.py" for path in baseline_changes):
        failures.append("Baseline source changes exceed the shared measurement harness")
    if baseline.get("worktree_dirty") and not baseline_changes:
        failures.append("Baseline source changes are not identified")
    if baseline_changes and not baseline.get("worktree_dirty"):
        failures.append("Baseline source fingerprint is inconsistent")
    if not baseline.get("harness_sha256") or baseline.get("harness_sha256") != candidate.get(
        "harness_sha256"
    ):
        failures.append("Before and after did not use the same identified measurement harness")
    for label, report, expected in (
        ("baseline", baseline, baseline_sha),
        ("candidate", candidate, candidate_sha),
    ):
        if expected is not None and report.get("revision") != expected:
            failures.append(label + " does not match the requested commit")
    workload_keys = (
        "scale",
        "history_bytes",
        "active_seconds",
        "idle_seconds",
        "idle_grace_seconds",
        "recovery_seconds",
        "convergence_seconds",
        "maintenance_convergence_seconds",
        "warmup_seconds",
        "interval_seconds",
        "ingest_per_interval",
    )
    if any(
        baseline.get("workload", {}).get(key) != candidate.get("workload", {}).get(key)
        for key in workload_keys
    ):
        failures.append("Before and after used different offered workloads")
    left = {phase["name"]: phase for phase in baseline.get("phases", [])}
    right = {phase["name"]: phase for phase in candidate.get("phases", [])}
    for name in ("active", "idle", "idle-expired", "recovery", "drain"):
        if name not in left or name not in right:
            failures.append(name + " phase is missing")
            continue
        before, after = left[name], right[name]
        if not all(math.isfinite(row["seconds"]) and row["seconds"] > 0 for row in (before, after)):
            failures.append(name + " has no measured duration")
            continue
        # Catch-up is finite work: compare its total cost, so finishing earlier
        # does not look worse solely because the same work ran in fewer minutes.
        before_factor = 1 if name == "drain" else 60 / before["seconds"]
        after_factor = 1 if name == "drain" else 60 / after["seconds"]
        for field in ("total_exec_time", "shared_blks_hit", "shared_blks_read", "wal_bytes"):
            old = before["cost"][field] * before_factor
            new = after["cost"][field] * after_factor
            if not all(math.isfinite(value) and value >= 0 for value in (old, new)):
                failures.append(name + ": invalid " + field)
                continue
            change = (new / old - 1) * 100 if old else None
            rows.append((name, field, old, new, change))
            # Resource tradeoffs remain visible; CPU/SQL, WAL and useful writer
            # behavior cannot regress materially just because one report is faster.
            if field in {"total_exec_time", "wal_bytes"} and old > 0 and new > old * 1.10:
                failures.append(name + ": " + field + " regressed by more than 10%")
        for label, old, new in (
            (
                "python_cpu_seconds",
                sum(row["cpu_seconds"] for row in before.get("processes", {}).values()),
                sum(row["cpu_seconds"] for row in after.get("processes", {}).values()),
            ),
            (
                "global_wal_bytes",
                before.get("database_cost", {}).get("global_wal_position_bytes"),
                after.get("database_cost", {}).get("global_wal_position_bytes"),
            ),
        ):
            if (
                old is None
                or new is None
                or (label == "python_cpu_seconds" and not before.get("processes"))
            ):
                failures.append(name + ": missing " + label)
                continue
            old *= before_factor
            new *= after_factor
            if not all(math.isfinite(value) and value >= 0 for value in (old, new)):
                failures.append(name + ": invalid " + label)
                continue
            rows.append((name, label, old, new, (new / old - 1) * 100 if old else None))
            if old > 0 and new > old * 1.10:
                failures.append(name + ": " + label + " regressed by more than 10%")
        for metric in ("ingested",):
            if before.get(metric, 0) and after.get(metric, 0) < before[metric] * 0.9:
                failures.append(name + ": useful throughput regressed")
    return {"passed": not failures, "failures": failures, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path, nargs="?")
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--absolute-only", action="store_true")
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--baseline-sha")
    parser.add_argument("--candidate-sha")
    args = parser.parse_args()
    if args.absolute_only != (args.baseline is None):
        parser.error("Provide a baseline for comparison, or --absolute-only for one candidate")
    try:
        candidate = json.loads(args.candidate.read_text())
        result = (
            absolute(candidate, candidate_sha=args.candidate_sha)
            if args.absolute_only
            else compare(
                json.loads(args.baseline.read_text()),
                candidate,
                baseline_sha=args.baseline_sha,
                candidate_sha=args.candidate_sha,
            )
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {
            "passed": False,
            "failures": ["Results unavailable: " + type(error).__name__],
            "rows": [],
        }
    lines = [
        "**" + ("PASS" if result["passed"] else "FAIL") + " — Sustained background work**",
        "",
        *(
            ["| Phase | Metric (per minute; drain total) | Measured |", "|---|---|---:|"]
            if args.absolute_only
            else [
                "| Phase | Metric (per minute; drain total) | Before | After | Change |",
                "|---|---|---:|---:|---:|",
            ]
        ),
    ]
    for phase, metric, before, after, delta in result["rows"]:
        change = f"{delta:+.1f}%" if delta is not None else "n/a"
        lines.append(
            f"| {phase} | {metric} | {after:.2f} |"
            if args.absolute_only
            else f"| {phase} | {metric} | {before:.2f} | {after:.2f} | {change} |"
        )
    if result["failures"]:
        lines.extend(["", *["- " + failure for failure in result["failures"]]])
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text("\n".join(lines) + "\n")
    return int(not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
