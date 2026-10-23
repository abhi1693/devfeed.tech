"""Profiling helpers preserve strict budgets and independent writer ownership."""

import asyncio
import runpy
from collections import Counter
from types import SimpleNamespace

import pytest

from tests.support.paths import REPO_ROOT

profile = SimpleNamespace(**runpy.run_path(str(REPO_ROOT / "scripts/profile_reader_views.py")))


@pytest.mark.parametrize(
    "label, ceiling",
    [
        ("starters-ordinary", 5),
        ("latest", 6),
        ("column-source", 4),
        ("columns-my-feed", 1),
        ("feed-view", 1),
    ],
)
def test_query_budgets_keep_existing_route_limits(label, ceiling):
    assert profile.query_budget(label) == ceiling
    assert profile.query_budget(label, {"other": 20}) == ceiling


def test_explicit_budget_only_changes_the_named_route_sql_ceiling():
    healthy = {
        "statuses": {"200": 100},
        "invalid_payloads": 0,
        "queries_min": 7,
        "queries_max": 7,
        "p95_ms": 20,
    }
    measurements = {"16": {"routes": {"recommended-ready": healthy}}}
    assert not profile.measurement_gates(measurements, 100)["passed"]
    assert profile.measurement_gates(measurements, 100, {"recommended-ready": 7})["passed"]
    failures = profile.route_failure_reasons(
        healthy | {"statuses": {503: 100}, "queries_min": 0, "p95_ms": 101}, 7, 100
    )
    assert failures == ["failed responses", "SQL budget", "p95 latency"]
    measurements["16"]["routes"]["recommended-ready"] = healthy | {"invalid_payloads": 1}
    assert not profile.measurement_gates(measurements, 100, {"recommended-ready": 7})["passed"]


@pytest.mark.parametrize("contention", [False, True])
def test_write_phase_keeps_account_ownership_and_existing_report_shape(contention):
    identities = {f"writer-{number}": {"owner": number} for number in range(6, 22)}
    counters: Counter[str | None] = Counter()
    active: Counter[int] = Counter()
    maximum: Counter[int] = Counter()
    owners = []

    class Client:
        async def put(self, _path, *, headers, json):
            owner = headers["owner"]
            owners.append(owner)
            active[owner] += 1
            maximum[owner] = max(maximum[owner], active[owner])
            counters[profile.REQUEST.get()] += 2
            await asyncio.sleep(0)
            active[owner] -= 1
            return SimpleNamespace(status_code=200, json=lambda: json)

    label, result = asyncio.run(
        profile.sample_write_phase(
            Client(), identities, None, counters, 33, (profile.FEED_VIEW_LABEL, "/view"), contention
        )
    )
    expected_accounts = {6} if contention else set(range(6, 22))
    assert set(owners) == expected_accounts
    assert max(maximum.values()) == (16 if contention else 1)
    assert label == f"feed-view-{'same-account' if contention else 'parallel-accounts'}"
    assert result["samples"] == len(result["raw_samples"]) == 33
    assert result["accounts"] == len(expected_accounts)
    assert result["concurrency"] == 16
    assert result["statuses"] == {200: 33}
    assert result["queries_min"] == result["queries_max"] == 2
    assert result["invalid_payloads"] == 0


def test_failed_write_restores_the_request_counting_context():
    class Client:
        async def put(self, *_args, **_kwargs):
            assert profile.REQUEST.get() == "failed-write"
            raise RuntimeError("transport failed")

    async def request():
        with pytest.raises(RuntimeError, match="transport failed"):
            await profile.sample_write_request(
                Client(), "/view", {}, {"view": "stacked"}, "failed-write", Counter()
            )
        assert profile.REQUEST.get() is None

    asyncio.run(request())


@pytest.mark.parametrize(
    "status, payload, label, service, expected",
    [
        (200, {"items": list(range(24))}, "latest", "public", (True, 24)),
        (200, {"items": []}, "latest", "public", (False, 0)),
        (200, {"items": []}, "recommended-ready", "user", (False, 0)),
        (200, {"view": "cards"}, "feed-view", "user", (True, None)),
        (200, [], "feed-view", "user", (False, None)),
        (500, {}, "latest", "public", (False, None)),
    ],
)
def test_feed_sample_guards_require_real_populated_pages(status, payload, label, service, expected):
    response = SimpleNamespace(status_code=status, json=lambda: payload)
    assert profile.response_shape(response, label, service) == expected


def test_failed_read_restores_the_request_counting_context():
    class Client:
        async def get(self, *_args, **_kwargs):
            assert profile.REQUEST.get() == "latest:1"
            raise RuntimeError("transport failed")

    async def request():
        with pytest.raises(RuntimeError, match="transport failed"):
            await profile.sample_api_request(
                {"public": Client()}, {}, Counter(), 1, ("latest", "public", "/feed", None)
            )
        assert profile.REQUEST.get() is None

    asyncio.run(request())
