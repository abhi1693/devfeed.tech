"""Regression decisions must not hide slow routes, noise, missing traffic or failures."""

import importlib.util
from datetime import datetime, timedelta

import pytest

from tests.support.paths import REPO_ROOT

spec = importlib.util.spec_from_file_location("load_compare", REPO_ROOT / "loadtests/compare.py")
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
        ([82, 100, 83], [120, 110, 82], "INCONCLUSIVE"),
        ([120, 110, 82], [82, 100, 83], "INCONCLUSIVE"),
        ([82, 100, 83], [120, 130, 100], "REGRESSED"),
        ([120, 130, 100], [82, 100, 83], "IMPROVED"),
        ([100, 100, 100], [120, 120, 120], "NO MATERIAL CHANGE"),
        ([100, 100, 100], [121, 121, 121], "REGRESSED"),
        ([200, 200, 200], [221, 221, 221], "NO MATERIAL CHANGE"),
        ([50, 50, 50], [61, 61, 61], "NO MATERIAL CHANGE"),
        ([100, 100], [150, 150], "ERROR"),
        ([0, 0, 0], [100, 100, 100], "ERROR"),
        ([100, 100, 100], [float("nan"), 100, 100], "ERROR"),
    ],
)
def test_repeated_latency_classification(base, head, expected):
    assert compare.classify(base, head) == expected


def runs():
    return {
        f"{side}-{index}": {
            "response_time_bucket_ms": 1,
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


def test_unconfirmed_pooled_shift_preserves_every_pair_and_discloses_uncertainty():
    data = runs()
    for side, values in (("base", [82, 100, 83]), ("head", [120, 110, 82])):
        for index, value in enumerate(values):
            data[f"{side}-{index}"]["entries"][4]["p95"] = value
    verdict, markdown = compare.report(data, "base", "head")
    assert verdict == "INCONCLUSIVE"
    assert "| Source feed | 83 | 110 | +27 (+32.5%) | Inconclusive |" in markdown
    assert (
        "| Source feed | 82 → 120 (+38, +46.3%) | 100 → 110 (+10, +10.0%) | "
        "83 → 82 (-1, -1.2%) | 1 | 0 |" in markdown
    )
    assert "does not prove unchanged performance" in markdown


def write_sample(root, side, index, sha):
    import json

    directory = root / f"{side}-{index}"
    directory.mkdir()
    (directory / "final.json").write_text(json.dumps(runs()[f"{side}-{index}"]))
    (directory / "exit-code.txt").write_text("0\n")
    (directory / "metadata.json").write_text(
        json.dumps(
            {
                **compare.PROTOCOL,
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
                "runner_name": f"runner-{index}",
                "architecture": "aarch64",
                "logical_cpus": 4,
                "started_at": (
                    "2026-01-01T00:00:00+00:00"
                    if (side == "base") == (index % 2 == 0)
                    else "2026-01-01T00:02:00+00:00"
                ),
            }
        )
    )
    started = datetime.fromisoformat(
        json.loads((directory / "metadata.json").read_text())["started_at"]
    )
    (directory / "measurement.json").write_text(
        json.dumps(
            {
                **compare.PROTOCOL,
                "phase": "finished",
                "users": 16,
                "ready_users": 16,
                "measurement_seconds": 60,
                "warmup_completed_seconds": 15.0,
                "measured_seconds": 60.0,
                "warmup_started_at": (started + timedelta(seconds=5)).isoformat(),
                "warmup_finished_at": (started + timedelta(seconds=20)).isoformat(),
                "started_at": (started + timedelta(seconds=20)).isoformat(),
                "finished_at": (started + timedelta(seconds=80)).isoformat(),
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
    "sample", [f"{side}-{index}" for side in ("base", "head") for index in range(3)]
)
def test_every_sample_requires_a_completed_measurement_phase(tmp_path, sample):
    for side in ("base", "head"):
        for index in range(3):
            write_sample(tmp_path, side, index, side)
    (tmp_path / sample / "measurement.json").unlink()
    collected = compare.collect(tmp_path, "base", "head")
    assert collected[sample]["invalid"]
    assert all(item["entries"] == runs()[key]["entries"] for key, item in collected.items())
    verdict, markdown = compare.report(collected, "base", "head")
    assert verdict == "ERROR"
    assert "No latency improvement claim" in markdown


@pytest.mark.parametrize(
    "filename,field,value",
    [
        ("metadata.json", "protocol_version", None),
        ("metadata.json", "protocol_version", "reader-load-v1"),
        ("metadata.json", "journey_seed", None),
        ("metadata.json", "journey_seed", 295),
        ("metadata.json", "journey_seed", True),
        ("metadata.json", "warmup_seconds", None),
        ("metadata.json", "warmup_seconds", 0),
        ("measurement.json", "protocol_version", "reader-load-v1"),
        ("measurement.json", "journey_seed", 295),
        ("measurement.json", "warmup_seconds", 14),
        ("measurement.json", "measurement_seconds", 59),
        ("measurement.json", "measurement_seconds", True),
        ("measurement.json", "users", 15),
        ("measurement.json", "ready_users", 15),
        ("measurement.json", "phase", "measuring"),
        ("measurement.json", "warmup_completed_seconds", 14.99),
        ("measurement.json", "measured_seconds", 59.99),
        ("measurement.json", "measured_seconds", float("nan")),
        ("measurement.json", "measured_seconds", "60"),
        ("measurement.json", "warmup_started_at", "2026-01-01T00:02:05"),
        ("measurement.json", "warmup_finished_at", "2026-01-01T00:02:15+00:00"),
        ("measurement.json", "started_at", "2026-01-01T00:02:19+00:00"),
        ("measurement.json", "finished_at", "2026-01-01T00:02:50+00:00"),
    ],
)
def test_old_mismatched_or_incomplete_phase_protocol_cannot_produce_a_latency_verdict(
    tmp_path, filename, field, value
):
    import json

    for side in ("base", "head"):
        for index in range(3):
            write_sample(tmp_path, side, index, side)
    path = tmp_path / "head-0" / filename
    document = json.loads(path.read_text())
    if value is None:
        document.pop(field)
    else:
        document[field] = value
    path.write_text(json.dumps(document))
    collected = compare.collect(tmp_path, "base", "head")
    assert collected["head-0"]["invalid"]
    assert collected["head-0"]["entries"] == runs()["head-0"]["entries"]
    assert compare.report(collected, "base", "head")[0] == "ERROR"


def test_internally_consistent_outlier_seed_still_cannot_mix_journeys_across_runners(tmp_path):
    import json

    for side in ("base", "head"):
        for index in range(3):
            write_sample(tmp_path, side, index, side)
    for filename in ("metadata.json", "measurement.json"):
        path = tmp_path / "head-1" / filename
        document = json.loads(path.read_text())
        document["journey_seed"] = 295
        path.write_text(json.dumps(document))
    collected = compare.collect(tmp_path, "base", "head")
    assert collected["head-1"]["invalid"]
    assert compare.report(collected, "base", "head")[0] == "ERROR"


def test_completed_locust_float_durations_match_runner_integer_settings(tmp_path):
    import json

    for side in ("base", "head"):
        for index in range(3):
            directory = write_sample(tmp_path, side, index, side)
            path = directory / "measurement.json"
            phase = json.loads(path.read_text())
            # Locust argparse emits float seconds; the runner's metadata records
            # the same requested windows as integers. JSON representation does
            # not change the completed protocol or the measured workload.
            phase["warmup_seconds"] = 15.0
            phase["measurement_seconds"] = 60.0
            path.write_text(json.dumps(phase))
    collected = compare.collect(tmp_path, "base", "head")
    assert not any(item.get("invalid") for item in collected.values())
    assert compare.report(collected, "base", "head")[0] == "NO MATERIAL CHANGE"


@pytest.mark.parametrize("phase_started_before_run", [False, True])
def test_phase_timestamps_must_belong_to_sequential_isolated_runner_samples(
    tmp_path, phase_started_before_run
):
    import json

    for side in ("base", "head"):
        for index in range(3):
            write_sample(tmp_path, side, index, side)
    path = tmp_path / "base-0/measurement.json"
    phase = json.loads(path.read_text())
    if phase_started_before_run:
        for field in ("warmup_started_at", "warmup_finished_at", "started_at", "finished_at"):
            phase[field] = (
                datetime.fromisoformat(phase[field]) - timedelta(seconds=10)
            ).isoformat()
    else:
        phase["finished_at"] = "2026-01-01T00:02:01+00:00"
    path.write_text(json.dumps(phase))
    collected = compare.collect(tmp_path, "base", "head")
    assert collected["base-0"]["invalid"] and collected["head-0"]["invalid"]
    assert compare.report(collected, "base", "head")[0] == "ERROR"


@pytest.mark.parametrize("precision", [None, 10, True])
def test_collector_rejects_unknown_or_mixed_histogram_precision(tmp_path, precision):
    import json

    for side in ("base", "head"):
        for index in range(3):
            write_sample(tmp_path, side, index, side)
    path = tmp_path / "head-1/final.json"
    sample = json.loads(path.read_text())
    if precision is None:
        sample.pop("response_time_bucket_ms")
    else:
        sample["response_time_bucket_ms"] = precision
    path.write_text(json.dumps(sample))
    collected = compare.collect(tmp_path, "base", "head")
    assert collected["head-1"]["invalid"]
    assert compare.report(collected, "base", "head")[0] == "ERROR"


@pytest.mark.parametrize(
    "changes",
    [
        [("head-0", "runner_name", None)],
        [("head-0", "runner_name", "different-runner")],
        [("head-0", "runner_name", " ")],
        [("base-1", "runner_name", "runner-0"), ("head-1", "runner_name", "runner-0")],
        [("head-0", "architecture", None)],
        [("head-0", "architecture", "x86_64")],
        [("head-0", "logical_cpus", None)],
        [("head-0", "logical_cpus", 8)],
        [("base-0", "logical_cpus", True), ("head-0", "logical_cpus", True)],
        [("head-0", "started_at", None)],
        [("head-0", "started_at", "not a timestamp")],
        [("head-0", "started_at", "2026-01-01T00:02:00")],
        [("head-0", "started_at", "2025-12-31T23:59:00+00:00")],
        [("base-1", "started_at", "2025-12-31T23:59:00+00:00")],
    ],
)
def test_unverifiable_runner_pairs_fail_closed_without_discarding_samples(
    tmp_path, monkeypatch, changes
):
    import json
    import sys

    for side in ("base", "head"):
        for index in range(3):
            write_sample(tmp_path, side, index, side)
    for sample, field, value in changes:
        path = tmp_path / sample / "metadata.json"
        metadata = json.loads(path.read_text())
        if value is None:
            metadata.pop(field)
        else:
            metadata[field] = value
        path.write_text(json.dumps(metadata))
    data = compare.collect(tmp_path, "base", "head")
    assert len(data) == 6
    assert all(item["entries"] == runs()[key]["entries"] for key, item in data.items())
    assert compare.report(data, "base", "head")[0] == "ERROR"
    monkeypatch.setattr(
        sys,
        "argv",
        ["compare.py", "--base-sha", "base", "--head-sha", "head", "--output", str(tmp_path)],
    )
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert compare.main() == 1


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
