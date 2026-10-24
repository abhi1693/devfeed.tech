"""Scale comments retain observed failures, signed costs, and missing-evidence states."""

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

import pytest

from tests.support.paths import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts/ci/catalog_refresh_report.py"
SPEC = importlib.util.spec_from_file_location("catalog_refresh_report", SCRIPT)
assert SPEC and SPEC.loader
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
SHARED_CHECKS = (
    "stable_reports_publish",
    "writer_commits_match_batches",
    "converges_after_writer_stops",
    "publication_generations_do_not_regress",
    "converged_cards_match_authoritative_lifecycle",
    "catalog_cursor_advances",
    "native_arm64_postgresql_17",
)
FIXED_CHECKS = ("publishes_every_requested_report", "reports_explicitly_catch_up")


def artifact(*, fixed):
    samples = []
    for index in range(20):
        results = []
        if fixed or index in (0, 7):
            for group, days in (("blockers", 1), ("overview", 30)):
                result = {"group": group, "days": days, "published": fixed}
                if fixed:
                    result["snapshot"] = {
                        "status": "catching_up",
                        "article_generation": index + 2,
                        "lag_seconds": 4.013627 if index == 0 else 3.0,
                        "catalog_lag_seconds": 64.0 + index * 4,
                    }
                else:
                    result["error"] = "ProjectionNotReady"
                results.append(result)
        samples.append(
            {
                "project": {"application_cpu_seconds": 3.0 if fixed else 2.0},
                "reports": {
                    "application_cpu_seconds": 0.2 if fixed else 0.025,
                    "result": results,
                },
            }
        )
    checks = {name: True for name in SHARED_CHECKS}
    checks.update(
        {name: True for name in FIXED_CHECKS}
        if fixed
        else {"recognizes_baseline_or_fixed_behavior": True}
    )
    return {
        "complete": True,
        "phase": "complete",
        "dataset": {"articles": 150000, "retained_article_cards": 55000},
        "workload": {"batches": 20, "batch": 200},
        "continuous": samples,
        "gates": {
            "passed": True,
            "observed_behavior": "fixed" if fixed else "baseline",
            "checks": checks,
        },
        "qualification": {"native_arm64_postgresql_17": True},
        "final_state": {"pending_events": 0},
        "legacy_lifecycle_parity": {"matches_legacy_lifecycle": True},
        "convergence": {
            "wall_seconds": 368.3212 if fixed else 341.6323,
            "application_cpu_seconds": 285.5021 if fixed else 287.3561,
        },
        "database_activity_delta": {"temp_bytes": 751748860 if fixed else 39729572},
    }


def render(before, after, *, job_result="success"):
    return report.render(
        report.summarize(before),
        report.summarize(after),
        job_result=job_result,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    )


def row(markdown, title):
    for line in markdown.splitlines():
        if line.startswith("| "):
            cells = [cell.strip().replace("**", "") for cell in line.split("|")[1:-1]]
            if cells and cells[0] == title:
                return cells[1:]
    raise AssertionError(f"Missing report row: {title}")


def test_comment_distinguishes_successful_reproduction_from_healthy_publication():
    status, markdown = render(artifact(fixed=False), artifact(fixed=True))
    assert status == "passed"
    assert "| Metric | Before | After | Delta (+/-) |" in markdown
    assert row(markdown, "Published / requested reports") == [
        "0/40",
        "40/40",
        "+40 reports (+100 pp)",
    ]
    assert row(markdown, "Actual report attempts") == ["4", "40", "+36 (+900.0%)"]
    assert row(markdown, "Report slots suppressed by cooldown") == [
        "36",
        "0",
        "-36 (-100.0%)",
    ]
    assert row(markdown, "ProjectionNotReady errors") == ["4", "0", "-4 (-100.0%)"]
    assert "original starvation was reproduced" in markdown
    assert "not that the baseline was healthy" in markdown
    assert "catching_up" in markdown


def test_comment_keeps_unavailable_lag_distinct_from_zero_and_exposes_true_costs():
    status, markdown = render(artifact(fixed=False), artifact(fixed=True))
    assert status == "passed"
    assert row(markdown, "Maximum operational lag") == [
        "Unavailable",
        "4.014 s",
        "Not comparable",
    ]
    assert row(markdown, "Recovery wall time") == [
        "341.63 s",
        "368.32 s",
        "+26.69 s (+7.8%)",
    ]
    assert row(markdown, "Projector application CPU (continuous phase)") == [
        "40.00 s",
        "60.00 s",
        "+20.00 s (+50.0%)",
    ]
    assert row(markdown, "Report application CPU (continuous phase)") == [
        "0.50 s",
        "4.00 s",
        "+3.50 s (+700.0%)",
    ]
    temporary = row(markdown, "Observed database temporary writes")
    assert temporary[:2] == ["39.73 MB", "751.75 MB"]
    assert temporary[2].startswith("+712.02 MB (+")
    assert "higher CPU, I/O or recovery time are additional cost" in markdown
    assert "unequal completed work" in markdown
    assert "not normalized per report" in markdown


def test_future_fixed_baseline_reports_zero_publication_change():
    status, markdown = render(artifact(fixed=True), artifact(fixed=True))
    assert status == "passed"
    assert row(markdown, "Published / requested reports") == [
        "40/40",
        "40/40",
        "+0 reports (+0 pp)",
    ]
    assert "original starvation was reproduced" not in markdown
    assert "starved baseline skips" not in markdown


@pytest.mark.parametrize("side", ["before", "after"])
def test_missing_side_is_unavailable_and_cannot_pass(side):
    before, after = artifact(fixed=False), artifact(fixed=True)
    if side == "before":
        before = None
    else:
        after = None
    status, markdown = render(before, after)
    assert status == "incomplete"
    assert row(markdown, "Published / requested reports")[0 if side == "before" else 1] == (
        "Unavailable"
    )
    assert "not a passing scale check" in markdown


@pytest.mark.parametrize("change", ["unfinished", "empty_samples", "partial_samples"])
def test_incomplete_samples_are_not_presented_as_zero_or_complete_publication(change):
    after = artifact(fixed=True)
    if change == "unfinished":
        after["complete"], after["phase"] = False, "continuous"
        after["continuous"] = []
    elif change == "empty_samples":
        after["continuous"] = []
    else:
        after["continuous"] = after["continuous"][:3]
    status, markdown = render(artifact(fixed=False), after)
    assert status != "passed"
    assert row(markdown, "Published / requested reports")[1] == "Unavailable"


@pytest.mark.parametrize(
    "field,value",
    [
        ("dataset", ["malformed"]),
        ("workload", ["malformed"]),
        ("gates", ["malformed"]),
        ("qualification", ["malformed"]),
        ("final_state", ["malformed"]),
        ("legacy_lifecycle_parity", ["malformed"]),
        ("convergence", ["malformed"]),
        ("continuous", "malformed"),
    ],
)
def test_malformed_nested_evidence_never_crashes_or_passes(field, value):
    after = artifact(fixed=True)
    after[field] = value
    status, markdown = render(artifact(fixed=False), after)
    assert status != "passed"
    assert "Unavailable" in markdown


@pytest.mark.parametrize("change", ["reports", "result", "published", "group", "error"])
def test_malformed_report_rows_cannot_borrow_copied_passing_gates(change):
    after = artifact(fixed=True)
    sample = after["continuous"][0]
    if change == "reports":
        sample["reports"] = ["malformed"]
    elif change == "result":
        sample["reports"]["result"] = ["malformed"]
    elif change == "published":
        sample["reports"]["result"][0]["published"] = "true"
    elif change == "group":
        sample["reports"]["result"][0]["group"] = "overview"
    else:
        sample["reports"]["result"][0]["error"] = "ProjectionNotReady"
    status, markdown = render(artifact(fixed=False), after)
    assert status != "passed"
    assert row(markdown, "Published / requested reports")[1] == "Unavailable"


@pytest.mark.parametrize("lag", [None, True, -1, float("nan"), float("inf"), "4"])
def test_unusable_lag_cannot_be_formatted_as_zero_or_pass(lag):
    after = artifact(fixed=True)
    after["continuous"][0]["reports"]["result"][0]["snapshot"]["lag_seconds"] = lag
    status, markdown = render(artifact(fixed=False), after)
    assert status != "passed"
    assert row(markdown, "Maximum operational lag")[1] == "Unavailable"


@pytest.mark.parametrize("check", SHARED_CHECKS + FIXED_CHECKS)
def test_candidate_requires_each_real_gate_even_if_passed_flag_is_copied(check):
    after = artifact(fixed=True)
    del after["gates"]["checks"][check]
    status, _markdown = render(artifact(fixed=False), after)
    assert status != "passed"


def test_complete_flag_and_observed_behavior_cannot_disguise_an_unfinished_phase():
    after = artifact(fixed=True)
    after["phase"] = "continuous"
    assert render(artifact(fixed=False), after)[0] != "passed"
    after = artifact(fixed=True)
    after["gates"]["observed_behavior"] = "baseline"
    assert render(artifact(fixed=False), after)[0] != "passed"


@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize("field", ["temporary_writes", "projector_cpu", "report_cpu"])
def test_missing_requested_cost_evidence_is_not_a_passing_comparison(side, field):
    before, after = artifact(fixed=False), artifact(fixed=True)
    evidence = before if side == "before" else after
    if field == "temporary_writes":
        del evidence["database_activity_delta"]["temp_bytes"]
        title = "Observed database temporary writes"
    else:
        group = "project" if field == "projector_cpu" else "reports"
        del evidence["continuous"][0][group]["application_cpu_seconds"]
        label = "Projector" if field == "projector_cpu" else "Report"
        title = f"{label} application CPU (continuous phase)"
    status, markdown = render(before, after)
    assert status != "passed"
    cells = row(markdown, title)
    assert cells[0 if side == "before" else 1] == "Unavailable"
    assert cells[2] == "Not comparable"


@pytest.mark.parametrize("change", ["unknown_errors", "failed_parity", "pending_events"])
def test_baseline_cannot_hide_other_failures_behind_a_passing_reproduction_flag(change):
    before = artifact(fixed=False)
    if change == "unknown_errors":
        for sample in before["continuous"]:
            for result in sample["reports"]["result"]:
                result["error"] = "TimeoutError"
    elif change == "failed_parity":
        before["legacy_lifecycle_parity"]["matches_legacy_lifecycle"] = False
    else:
        before["final_state"]["pending_events"] = 1
    status, markdown = render(before, artifact(fixed=True))
    assert status != "passed"
    assert "not a passing scale check" in markdown


@pytest.mark.parametrize("change", ["lost_publication", "stale_operational_lag"])
def test_future_fixed_baseline_must_still_prove_complete_healthy_publication(change):
    before = artifact(fixed=True)
    result = before["continuous"][0]["reports"]["result"][0]
    if change == "lost_publication":
        result.update(published=False, error="ProjectionNotReady")
        del result["snapshot"]
    else:
        result["snapshot"]["lag_seconds"] = 61.0
    assert render(before, artifact(fixed=True))[0] != "passed"


def cli(tmp_path, *, job_result="success", changes=None, output=None, metadata=None):
    reports = tmp_path / "reports"
    reports.mkdir(exist_ok=True)
    for side, fixed in (("baseline", False), ("candidate", True)):
        path = reports / f"{side}.json"
        if not path.exists():
            path.write_text(json.dumps(artifact(fixed=fixed)))
    output = output or tmp_path / "summary.md"
    metadata = metadata or tmp_path / "metadata.json"
    environment = {
        **os.environ,
        "BASE_SHA": BASE_SHA,
        "HEAD_SHA": HEAD_SHA,
        "GITHUB_RUN_ID": "123456",
        "GITHUB_RUN_ATTEMPT": "2",
        **(changes or {}),
    }
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--reports",
            str(reports),
            "--output",
            str(output),
            "--metadata",
            str(metadata),
            "--job-result",
            job_result,
        ],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    return result, output, metadata


def test_cli_writes_a_hash_bound_to_exact_head_run_and_attempt(tmp_path):
    result, output, metadata = cli(tmp_path)
    assert result.returncode == 0, result.stderr
    document = json.loads(metadata.read_text())
    assert document == {
        "schema": 1,
        "run_id": "123456",
        "run_attempt": 2,
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "status": "passed",
        "summary_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    assert BASE_SHA[:12] in output.read_text()
    assert HEAD_SHA[:12] in output.read_text()


@pytest.mark.parametrize("job_result", ["failure", "cancelled"])
def test_cli_does_not_green_a_failed_or_cancelled_measurement(tmp_path, job_result):
    result, output, metadata = cli(tmp_path, job_result=job_result)
    assert result.returncode != 0
    assert json.loads(metadata.read_text())["status"] != "passed"
    assert "not a passing scale check" in output.read_text()


def test_cli_preserves_unavailable_evidence_for_bad_json(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "candidate.json").write_text("not JSON containing private-internal-detail")
    result, output, metadata = cli(tmp_path)
    assert result.returncode != 0
    assert json.loads(metadata.read_text())["status"] != "passed"
    assert "Unavailable" in output.read_text()
    assert "private-internal-detail" not in output.read_text()


@pytest.mark.parametrize(
    "changes",
    [
        {"GITHUB_RUN_ID": "missing"},
        {"GITHUB_RUN_ATTEMPT": "0"},
        {"BASE_SHA": "short"},
        {"HEAD_SHA": "B" * 40},
    ],
)
def test_cli_requires_unambiguous_source_identity(tmp_path, changes):
    result, output, metadata = cli(tmp_path, changes=changes)
    assert result.returncode != 0
    assert not output.exists()
    assert not metadata.exists()


@pytest.mark.parametrize("destination", ["summary", "metadata"])
def test_cli_fails_when_a_required_output_cannot_be_written(tmp_path, destination):
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    result, _output, metadata = cli(
        tmp_path,
        output=occupied if destination == "summary" else None,
        metadata=occupied if destination == "metadata" else None,
    )
    assert result.returncode != 0
    assert not metadata.is_file()


def test_reader_refuses_symlinks_and_oversized_documents(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(artifact(fixed=True)))
    linked = tmp_path / "linked.json"
    linked.symlink_to(source)
    assert report.read_document(linked)[0] is None
    monkeypatch.setattr(report, "MAX_DOCUMENT_BYTES", 10)
    assert report.read_document(source)[0] is None


def test_rendering_does_not_mutate_original_evidence():
    before, after = artifact(fixed=False), artifact(fixed=True)
    original = copy.deepcopy((before, after))
    render(before, after)
    assert (before, after) == original
