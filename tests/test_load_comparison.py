"""Regression decisions must not hide slow routes, noise, missing traffic or failures."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "load_compare", Path(__file__).resolve().parents[1] / "loadtests/compare.py"
)
assert spec and spec.loader
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


@pytest.mark.parametrize(
    "base,head,expected",
    [
        ([100, 105, 95], [150, 155, 145], "REGRESSED"),
        ([150, 155, 145], [100, 105, 95], "IMPROVED"),
        ([10, 10, 10], [15, 15, 15], "NO MATERIAL CHANGE"),
        ([5, 10, 5], [5, 5, 10], "NO MATERIAL CHANGE"),
        ([100, 100, 100], [110, 110, 110], "NO MATERIAL CHANGE"),
        ([100, 100, 100], [100, 150, 100], "INCONCLUSIVE"),
        ([100, 160, 100], [140, 140, 140], "INCONCLUSIVE"),
        ([0, 0, 0], [100, 100, 100], "ERROR"),
        ([100, 100, 100], [float("nan"), 100, 100], "ERROR"),
    ],
)
def test_repeated_latency_classification(base, head, expected):
    assert compare.classify(base, head) == expected


def runs():
    return {
        f"{side}-{index}": {
            "exit_code": 0,
            "gate_failures": [],
            "entries": [
                {
                    "name": route,
                    "requests": 100,
                    "failures": 0,
                    "p50": 50,
                    "p95": 100,
                    "p99": 150,
                    "rps": 4,
                }
                for route in compare.ROUTES
            ],
        }
        for side in ("base", "head")
        for index in range(3)
    }


def test_single_slow_endpoint_cannot_hide_in_aggregate():
    data = runs()
    for index in range(3):
        data[f"head-{index}"]["entries"][1]["p95"] = 160
    verdict, markdown = compare.report(data, "base-sha", "head-sha")
    assert verdict == "REGRESSED"
    assert "+60.0%" in markdown
    assert "base-sha" in markdown and "head-sha" in markdown


def test_clean_and_improved_runs():
    data = runs()
    assert compare.report(data, "base", "head")[0] == "NO MATERIAL CHANGE"
    for index in range(3):
        data[f"head-{index}"]["entries"][1]["p95"] = 60
    assert compare.report(data, "base", "head")[0] == "IMPROVED"


def test_failed_baseline_is_not_reported_as_an_improvement():
    data = runs()
    data["base-0"]["exit_code"] = 1
    assert compare.report(data, "base", "head")[0] == "ERROR"
    assert compare.report({}, "base", "head")[0] == "ERROR"


@pytest.mark.parametrize("field,value", [("requests", 0), ("failures", 1)])
def test_missing_traffic_and_new_errors_fail(field, value):
    data = runs()
    data["head-0"]["entries"][1][field] = value
    assert compare.report(data, "base", "head")[0] == "REGRESSED"


def test_connection_ownership_failure_fails_even_with_good_http_metrics():
    data = runs()
    data["head-2"]["exit_code"] = 1
    assert compare.report(data, "base", "head")[0] == "REGRESSED"


def write_sample(root, side, index, sha):
    import json

    directory = root / f"{side}-{index}"
    directory.mkdir()
    (directory / "final.json").write_text(json.dumps(runs()[f"{side}-{index}"]))
    (directory / "exit-code.txt").write_text("0\n")
    (directory / "metadata.json").write_text(
        json.dumps(
            {
                "commit": sha,
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
        )
    )
    return directory


def test_collector_checks_commits_and_requires_all_six_artifacts(tmp_path):
    for side in ("base", "head"):
        for index in range(3):
            write_sample(tmp_path, side, index, side)
    data = compare.collect(tmp_path, "base", "head")
    assert compare.report(data, "base", "head")[0] == "NO MATERIAL CHANGE"
    (tmp_path / "head-0" / "final.json").unlink()
    data = compare.collect(tmp_path, "base", "head")
    assert data["head-0"]["invalid"]
    assert compare.report(data, "base", "head")[0] == "ERROR"
    data = compare.collect(tmp_path, "wrong-base-sha", "head")
    assert data["base-0"]["invalid"]


@pytest.mark.parametrize(
    "filename,contents",
    [
        ("final.json", "{}"),
        ("final.json", "[1]"),
        ("metadata.json", "{}"),
        ("metadata.json", "not json"),
        ("exit-code.txt", "incomplete"),
    ],
)
def test_corrupted_or_failed_setup_artifacts_cannot_pass(tmp_path, filename, contents):
    directory = write_sample(tmp_path, "head", 0, "head")
    (directory / filename).write_text(contents)
    assert compare.collect(tmp_path, "base", "head")["head-0"]["invalid"]


@pytest.mark.parametrize(
    "scenario,expected_verdict,expected_exit",
    [
        ("noise", "INCONCLUSIVE", 0),
        ("regression", "REGRESSED", 1),
        ("missing", "ERROR", 1),
        ("invalid_latency", "ERROR", 1),
        ("baseline_failure", "ERROR", 1),
        ("head_failure", "REGRESSED", 1),
        ("unchanged", "NO MATERIAL CHANGE", 0),
        ("improved", "IMPROVED", 0),
    ],
)
def test_cli_exit_matches_report_verdict(
    tmp_path, monkeypatch, scenario, expected_verdict, expected_exit
):
    import json
    import sys

    data = runs()
    if scenario == "noise":
        data["base-0"]["entries"][1]["p95"] = 160
    elif scenario in {"regression", "improved"}:
        for index in range(3):
            data[f"head-{index}"]["entries"][1]["p95"] = 160 if scenario == "regression" else 60
    elif scenario == "invalid_latency":
        for index in range(3):
            data[f"head-{index}"]["entries"][1]["p95"] = 0
    elif scenario == "missing":
        data["head-0"] = {"invalid": True, "exit_code": None}
    elif scenario in {"baseline_failure", "head_failure"}:
        data["base-0" if scenario == "baseline_failure" else "head-0"]["exit_code"] = 1
    monkeypatch.setattr(compare, "collect", lambda *args: data)
    monkeypatch.setattr(
        sys,
        "argv",
        ["compare.py", "--base-sha", "base", "--head-sha", "head", "--output", str(tmp_path)],
    )
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "job-summary.md"))
    assert compare.main() == expected_exit
    assert json.loads((tmp_path / "comparison.json").read_text())["verdict"] == expected_verdict
    markdown = (tmp_path / "summary.md").read_text()
    assert expected_verdict in markdown
    assert (tmp_path / "job-summary.md").read_text() == markdown
    if scenario == "noise":
        assert "non-blocking" in markdown
