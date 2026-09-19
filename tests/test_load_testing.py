"""Catch false-positive load reports without importing Locust/gevent into pytest."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "load_policy", Path(__file__).resolve().parents[1] / "loadtests/policy.py"
)
assert spec and spec.loader
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


@pytest.mark.parametrize(
    "host", ["http://127.0.0.1:8000", "http://localhost:8080", "http://[::1]:80"]
)
def test_loopback_load_targets_do_not_need_remote_opt_in(host):
    policy.validate_target(host)


@pytest.mark.parametrize(
    "host",
    ["https://devfeed.tech", "http://localhost.example.com", "http://10.0.0.1", ""],
)
def test_remote_load_is_explicit(host):
    with pytest.raises(ValueError):
        policy.validate_target(host)


def test_remote_opt_in_does_not_accept_credentials_or_redirect_shaped_targets():
    policy.validate_target("https://staging.example.com", True)
    for host in (
        "file:///tmp/test",
        "https://user:secret@example.com",
        "https://example.com/?token=x",
    ):
        with pytest.raises(ValueError):
            policy.validate_target(host, True)


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("feed", "<html>Login</html>"),
        ("feed", {"items": [{"id": "x"}], "next_cursor": None}),
        ("article", {"detail": "unavailable"}),
        ("topics", {"items": []}),
        ("sources", [{"name": "Publisher"}]),
        ("options", {}),
        ("search", {"sections": {}}),
    ],
)
def test_success_status_with_wrong_content_is_not_a_successful_journey(kind, payload):
    assert not policy.valid_payload(kind, payload)


def stats(name, requests=100, failed=0, p95=100):
    return SimpleNamespace(
        name=name,
        num_requests=requests,
        num_failures=failed,
        fail_ratio=failed / requests if requests else 0,
        get_response_time_percentile=lambda _: p95,
    )


def gate(total, entries):
    return policy.gate_failures(
        total, entries, minimum=50, max_failure_ratio=0, max_p95_ms=2000, required={"article"}
    )


def test_zero_traffic_and_missing_journeys_cannot_pass():
    assert len(gate(stats("total", requests=0), [])) == 2
    assert gate(stats("total"), [stats("feed")]) == ["No successful samples for article"]


def test_overload_and_slow_endpoints_cannot_hide_in_fast_aggregate():
    assert gate(stats("total", failed=1), [stats("article")])
    assert gate(stats("total"), [stats("article", p95=2500)])
    assert not gate(stats("total"), [stats("article")])


def test_search_workload_has_named_query_variants_and_strict_gate():
    locustfile = (Path(__file__).resolve().parents[1] / "loadtests/locustfile.py").read_text()
    for name in (
        "/v1/search [exact]",
        "/v1/search [typo]",
        "/v1/search [natural]",
        "/v1/search [zero]",
    ):
        assert name in locustfile
    assert "search-max-p95-ms" in locustfile
