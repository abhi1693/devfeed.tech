"""The PR report must expose regressions and never turn partial evidence into a pass."""

import copy
import importlib.util
import json
import os
import runpy
import sys
from types import SimpleNamespace

import pytest

from tests.support.paths import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts/ci/token-usage-summary.py"
SPEC = importlib.util.spec_from_file_location("token_usage_summary", SCRIPT)
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)
HEAD = "1234567890abcdef" * 2 + "12345678"
RUN = "https://github.com/abhi1693/devfeed.tech/actions/runs/12345"


def profile():
    histories = {
        kind: dict(zip(summary.FIELDS, (10000, 100, 500, 1000), strict=True))
        for kind in summary.KINDS
    }
    data = {
        "client_architecture": "aarch64",
        "production_volume": {
            "articles": 90000,
            "histories": histories,
            "observed_at": "2026-10-24T10:05:40Z",
        },
        "scales": [],
    }
    for size in summary.SCALES:
        scale = {
            "articles": size,
            "histories": copy.deepcopy(histories),
            "production_volume_checks": {
                "articles": True,
                **{f"{kind}.{field}": True for kind in summary.KINDS for field in summary.FIELDS},
            },
            "reports": [],
        }
        for days in summary.WINDOWS:
            for mode in summary.MODES:
                report = {
                    "days": days,
                    "plan_mode": mode,
                    "result_equality": True,
                    "checks": dict.fromkeys(summary.CHECKS, True),
                    "strategies": {},
                }
                for name, elapsed in (("original", 100), ("current", 75)):
                    report["strategies"][name] = {
                        "median_ms": elapsed,
                        "samples": [
                            {
                                "elapsed_ms": elapsed,
                                "application_cpu_ms": elapsed / 100,
                                "database_container_cpu_ms": elapsed / 2,
                            }
                        ],
                        "buffers": {
                            "Temp Written Blocks": 10 if name == "original" else 0,
                            "Temp Read Blocks": 0,
                        },
                        "aggregates": [{"Plan Rows": 200, "Peak Memory Usage": 40}],
                        "ingestion": [
                            {
                                "insert_and_finish": 3,
                                "completed_during_report": 2,
                                "latency_ms": [1, 2, 3],
                            }
                        ],
                    }
                scale["reports"].append(report)
        data["scales"].append(scale)
    return data


def render(data, outcome="success", **context):
    return summary.build_summary(
        data,
        head_sha=context.get("head_sha", HEAD),
        run_url=context.get("run_url", RUN),
        outcome=outcome,
    )


def run_cli(monkeypatch, directory, *, outcome="success", extra_args=()):
    monkeypatch.chdir(directory)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--head-sha",
            HEAD,
            "--run-url",
            RUN,
            "--outcome",
            outcome,
            *extra_args,
        ],
    )
    try:
        summary.main()
    except SystemExit as error:
        return error.code
    return 0


def test_complete_success_lists_every_case_resource_units_and_evidence():
    result = render(profile())
    assert result.startswith("**PASS")
    assert "| 100,000 | 1d | custom | 100.00 | 75.00 | +25.0% |" in result
    assert "| 300,000 | 30d | generic | 100.00 | 75.00 | +25.0% |" in result
    assert result.count("| 100.00 | 75.00 | +25.0% |") == 12
    assert "| Runtime comparisons | 12 / 12 |" in result
    assert "| Identical token accounting | 12 / 12 |" in result
    assert "| Runtime, plan and spill gates | 84 / 84 |" in result
    assert "| Production-volume gates | 26 / 26 |" in result
    assert "DB container CPU (ms)" in result
    assert "Largest hash aggregate (KiB)" in result
    assert "Temp writes (blocks)" in result
    assert f"Compared head: `{HEAD}`" in result
    assert f"[Benchmark run]({RUN})" in result
    assert "+ means better, − means worse" in result
    assert "not a live production benchmark" in result
    assert "Production snapshot observed at: `2026-10-24T10:05:40Z`" in result
    assert result.count("<details>") == 2
    assert result.index("| Runtime comparisons |") < result.index("<details>")


def test_negative_regression_stays_visible_even_inside_runtime_noise_allowance():
    data = profile()
    data["scales"][0]["reports"][0]["strategies"]["current"]["median_ms"] = 110
    result = render(data)
    assert result.startswith("**PASS")
    assert "| 100,000 | 1d | custom | 100.00 | 110.00 | -10.0% |" in result
    assert "PASS does not mean every case got faster" in result


@pytest.mark.parametrize("damage", ["report", "scale", "check", "samples", "buffers", "ingestion"])
def test_missing_required_evidence_never_passes(damage):
    data = profile()
    report = data["scales"][0]["reports"][0]
    if damage == "report":
        data["scales"][0]["reports"].pop()
    elif damage == "scale":
        data["scales"].pop()
    elif damage == "check":
        report["checks"].pop("temporary_writes_zero")
    else:
        report["strategies"]["current"].pop(damage)
    result = render(data)
    assert result.startswith("**INCOMPLETE")
    assert "Missing or invalid evidence" in result
    if damage == "report":
        assert "| Runtime comparisons | 11 / 12 |" in result
        assert "| 100,000 | 30d | generic | n/a | n/a | n/a |" in result


@pytest.mark.parametrize(
    "outcome, verdict",
    [("failure", "FAIL"), ("cancelled", "INCOMPLETE"), ("skipped", "INCOMPLETE")],
)
def test_measurement_outcome_overrides_otherwise_passing_json(outcome, verdict):
    assert render(profile(), outcome=outcome).startswith(f"**{verdict}")


@pytest.mark.parametrize("check", summary.CHECKS)
def test_failed_plan_gate_is_named_and_has_priority_over_missing_evidence(check):
    data = profile()
    data["scales"][0]["reports"][0]["checks"][check] = False
    data["scales"][1]["reports"].pop()
    result = render(data)
    assert result.startswith("**FAIL")
    assert f"100,000 articles / 1d / custom: {check}" in result
    assert "| Runtime, plan and spill gates | 76 / 84 |" in result


def test_accounting_and_actual_production_counts_are_checked_independently_of_recorded_gate():
    data = profile()
    data["scales"][0]["reports"][0]["result_equality"] = False
    data["scales"][0]["histories"]["research_verification"]["thirty_day_rows"] = 999
    result = render(data)
    assert result.startswith("**FAIL")
    assert "token accounting differs" in result
    assert "research_verification.thirty_day_rows" in result
    assert "999 (FAIL)" in result
    assert "| Production-volume gates | 25 / 26 |" in result


@pytest.mark.parametrize("value", [None, True, "12.3", -1, float("nan"), float("inf"), 10**400])
def test_invalid_runtime_cannot_pass_or_be_formatted_as_a_real_measurement(value):
    data = profile()
    data["scales"][0]["reports"][0]["strategies"]["current"]["median_ms"] = value
    result = render(data)
    assert result.startswith("**INCOMPLETE")
    assert "| 100,000 | 1d | custom | 100.00 | n/a | n/a |" in result


@pytest.mark.parametrize("field", ["elapsed_ms", "application_cpu_ms", "database_container_cpu_ms"])
def test_nonfinite_cpu_and_runtime_samples_cannot_pass(field):
    data = profile()
    data["scales"][0]["reports"][0]["strategies"]["current"]["samples"][0][field] = float("nan")
    assert render(data).startswith("**INCOMPLETE")


def test_invalid_hash_memory_cannot_pass_while_sorted_aggregate_can_omit_it():
    data = profile()
    aggregate = data["scales"][0]["reports"][0]["strategies"]["current"]["aggregates"][0]
    aggregate["Peak Memory Usage"] = float("nan")
    assert render(data).startswith("**INCOMPLETE")
    aggregate.pop("Peak Memory Usage")
    assert render(data).startswith("**PASS")


def test_zero_baseline_has_no_division_by_zero_or_fabricated_percentage():
    data = profile()
    data["scales"][0]["reports"][0]["strategies"]["original"]["median_ms"] = 0
    result = render(data)
    assert "| 100,000 | 1d | custom | 0.00 | 75.00 | n/a |" in result
    assert "0.00 | 0.00 | n/a" in result or "zero baseline" in result


def test_writer_startup_outlier_exposes_signed_regression_and_sample_size():
    data = profile()
    data["scales"][0]["reports"][0]["strategies"]["current"]["ingestion"][0]["latency_ms"] = [
        1,
        2,
        30,
    ]
    result = render(data)
    assert result.startswith("**PASS")
    assert "Writer latency caveats" in result
    assert "| 100,000 / 1d / custom | 3.00 | 30.00 | -900.0% | 3 / 3 |" in result
    assert "samples include writer startup" in result
    assert "has no pass/fail gate" in result


@pytest.mark.parametrize(
    "invalid",
    [
        [],
        None,
        1,
        {"scales": "wrong"},
        {"scales": [{}]},
        {"scales": [{"articles": 100000, "reports": [None]}]},
    ],
)
def test_malformed_schema_is_reported_without_crashing(invalid):
    assert render(invalid).startswith("**INCOMPLETE")


@pytest.mark.parametrize("contents", [None, "not json", "{", b"\xff"])
def test_missing_and_malformed_files_create_readable_incomplete_report(
    tmp_path, monkeypatch, contents
):
    directory = tmp_path / "reports/token-usage"
    directory.mkdir(parents=True)
    artifact = directory / "profile.json"
    if isinstance(contents, bytes):
        artifact.write_bytes(contents)
    elif contents is not None:
        artifact.write_text(contents)
    assert run_cli(monkeypatch, tmp_path) == 1
    result = (directory / "summary.md").read_text()
    assert result.startswith("**INCOMPLETE")
    assert "Profile artifact is missing, unreadable or malformed" in result
    assert "| 100,000 | 1d | custom | n/a | n/a | n/a |" in result


def test_duplicate_or_unexpected_matrix_entries_are_incomplete():
    data = profile()
    data["scales"][0]["reports"].append(copy.deepcopy(data["scales"][0]["reports"][0]))
    assert render(data).startswith("**INCOMPLETE")
    data["scales"][0]["reports"][-1]["plan_mode"] = "evil|\n<script>"
    result = render(data, head_sha="bad`\n|<script>", run_url="javascript:alert(1)")
    assert result.startswith("**INCOMPLETE")
    assert "evil" not in result
    assert "<script>" not in result
    assert "javascript:" not in result
    assert "Benchmark run link unavailable" in result


def test_cli_writes_result_to_fixed_file_without_database_dependencies(tmp_path, monkeypatch):
    directory = tmp_path / "reports/token-usage"
    directory.mkdir(parents=True)
    artifact = directory / "profile.json"
    output = directory / "summary.md"
    artifact.write_text(json.dumps(profile()))
    output.write_text("previous comment" * 1000)
    assert run_cli(monkeypatch, tmp_path) == 0
    assert output.read_text().startswith("**PASS")
    assert "previous comment" not in output.read_text()


def test_script_entrypoint_reads_and_writes_only_fixed_artifact_names(tmp_path, monkeypatch):
    directory = tmp_path / "reports/token-usage"
    directory.mkdir(parents=True)
    (directory / "profile.json").write_text(json.dumps(profile()))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--head-sha", HEAD, "--run-url", RUN, "--outcome", "success"],
    )
    runpy.run_path(str(SCRIPT), run_name="__main__")
    assert (directory / "summary.md").read_text().startswith("**PASS")


@pytest.mark.parametrize("outcome,verdict", [("success", "INCOMPLETE"), ("failure", "FAIL")])
def test_cli_persists_unsuccessful_summary_before_exiting_nonzero(
    tmp_path, monkeypatch, outcome, verdict
):
    output = tmp_path / "reports/token-usage/summary.md"
    assert run_cli(monkeypatch, tmp_path, outcome=outcome) == 1
    assert output.read_text().startswith(f"**{verdict}")


@pytest.mark.parametrize("option", ["--profile", "--output"])
def test_cli_rejects_arbitrary_filesystem_paths(tmp_path, monkeypatch, option):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    assert run_cli(monkeypatch, tmp_path, extra_args=(option, str(outside))) == 2
    assert outside.read_text() == "keep"
    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize("component", ["reports", "reports/token-usage"])
def test_cli_rejects_symlinks_in_report_directory(tmp_path, monkeypatch, component):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / component
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    assert run_cli(monkeypatch, tmp_path) == 2
    assert not list(outside.iterdir())


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo"])
def test_output_cannot_redirect_or_block_writes(tmp_path, monkeypatch, kind):
    directory = tmp_path / "reports/token-usage"
    directory.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("keep")
    output = directory / "summary.md"
    if kind == "symlink":
        output.symlink_to(outside)
    elif kind == "hardlink":
        output.hardlink_to(outside)
    elif kind == "directory":
        output.mkdir()
    else:
        os.mkfifo(output)
    assert run_cli(monkeypatch, tmp_path) == 2
    assert outside.read_text() == "keep"


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo", "oversized"])
def test_unsafe_profile_files_produce_incomplete_evidence(tmp_path, monkeypatch, kind):
    directory = tmp_path / "reports/token-usage"
    directory.mkdir(parents=True)
    artifact = directory / "profile.json"
    if kind == "symlink":
        outside = tmp_path / "outside.json"
        outside.write_text(json.dumps(profile()))
        artifact.symlink_to(outside)
    elif kind == "directory":
        artifact.mkdir()
    elif kind == "fifo":
        os.mkfifo(artifact)
    else:
        monkeypatch.setattr(summary, "MAX_PROFILE_BYTES", 8)
        artifact.write_bytes(b" " * 9)
    assert run_cli(monkeypatch, tmp_path) == 1
    assert (directory / "summary.md").read_text().startswith("**INCOMPLETE")


def test_input_read_stays_bounded_if_profile_grows_after_size_check(tmp_path, monkeypatch):
    monkeypatch.setattr(summary, "MAX_PROFILE_BYTES", 8)
    artifact = tmp_path / "profile.json"
    artifact.write_bytes(b" " * 9)
    actual_fstat = os.fstat

    def outdated_size(descriptor):
        metadata = actual_fstat(descriptor)
        return SimpleNamespace(st_mode=metadata.st_mode, st_size=0)

    monkeypatch.setattr(summary.os, "fstat", outdated_size)
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert summary.read_profile(descriptor) is None
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("field,value", [("insert_and_finish", 4), ("completed_during_report", 4)])
def test_inconsistent_writer_counts_cannot_pass(field, value):
    data = profile()
    data["scales"][0]["reports"][0]["strategies"]["current"]["ingestion"][0][field] = value
    result = render(data)
    assert result.startswith("**INCOMPLETE")
    assert "writer measurements missing or invalid" in result
