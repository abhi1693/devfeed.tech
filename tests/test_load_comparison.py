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
        ([0, 0, 0], [100, 100, 100], "INCONCLUSIVE"),
        ([100, 100, 100], [float("nan"), 100, 100], "INCONCLUSIVE"),
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
    assert compare.report(data, "base", "head")[0] == "INCONCLUSIVE"
    assert compare.report({}, "base", "head")[0] == "INCONCLUSIVE"


@pytest.mark.parametrize("field,value", [("requests", 0), ("failures", 1)])
def test_missing_traffic_and_new_errors_fail(field, value):
    data = runs()
    data["head-0"]["entries"][1][field] = value
    assert compare.report(data, "base", "head")[0] == "REGRESSED"


def test_connection_ownership_failure_fails_even_with_good_http_metrics():
    data = runs()
    data["head-2"]["exit_code"] = 1
    assert compare.report(data, "base", "head")[0] == "REGRESSED"
