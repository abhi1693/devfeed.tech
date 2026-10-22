"""Transient solver receipts enforce size, credential scope and cleanup boundaries."""

import base64
import json
from types import SimpleNamespace

import httpcore
import pytest
from devfeed_core.config import get_settings
from devfeed_core.feeds import queued_solver
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.feeds.solver_providers import flaresolverr
from devfeed_core.feeds.solver_types import SolveRequest
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.exceptions import NoSuchJobError
from rq.job import JobStatus
from test_fetcher import use_pool
from test_source_readiness_unit import NOW
from test_source_solver import URL
from test_source_solver import queued as queued


@pytest.mark.parametrize("size", [1025, 1030])
def test_queued_solver_rejects_oversized_encoded_and_decoded_receipts(queued, monkeypatch, size):
    monkeypatch.setattr(get_settings(), "feed_max_bytes", 1024)
    queued.job.return_value.return_value = {
        "url": URL,
        "body": base64.b64encode(b"a" * size).decode(),
    }
    with pytest.raises(FeedError) as raised:
        queued_solver.solve_feed(URL)
    assert raised.value.reason == "response_too_large"
    queued.job.delete.assert_called_once_with()


@pytest.mark.parametrize("status", [JobStatus.FAILED, JobStatus.CANCELED, JobStatus.STOPPED])
def test_terminal_solver_work_returns_a_safe_retry_without_reading_a_partial_result(queued, status):
    queued.job.get_status.return_value = status
    with pytest.raises(FeedError) as raised:
        queued_solver.solve_feed(URL)
    assert raised.value.reason == "solver_unavailable" and raised.value.retryable
    assert raised.value.retry_after == 60
    queued.job.return_value.assert_not_called()
    queued.job.delete.assert_called_once_with()
    queued.job.cancel.assert_not_called()


def test_job_expiry_during_polling_is_a_safe_retry_and_cleanup_does_not_mask_it(queued):
    queued.job.get_status.side_effect = NoSuchJobError("private work receipt")
    with pytest.raises(FeedError) as raised:
        queued_solver.solve_feed(URL)
    assert raised.value.reason == "solver_unavailable" and raised.value.retry_after == 60
    assert "private" not in str(raised.value)
    queued.job.return_value.assert_not_called()
    queued.job.cancel.assert_not_called()
    queued.job.delete.assert_not_called()


def test_cleanup_redis_outage_does_not_discard_an_already_received_valid_result(queued):
    queued.job.get_status.side_effect = [
        JobStatus.FINISHED,
        RedisConnectionError("private endpoint"),
    ]
    result = queued_solver.solve_feed(URL)
    assert result.body == b"feed" and result.final_url == URL
    queued.job.cancel.assert_not_called()
    queued.job.delete.assert_not_called()


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_image_cookie_replay_drops_expired_credentials_and_never_sends_secure_cookies_over_http(
    monkeypatch, scheme
):
    monkeypatch.setattr(flaresolverr, "time", SimpleNamespace(time=lambda: NOW.timestamp()))
    cookies = [
        {"name": "secure", "value": "clearance", "secure": True, "expires": NOW.timestamp() + 60},
        {"name": "expired", "value": "old", "expires": NOW.timestamp() - 60},
        {"name": "session", "value": "current", "expires": -1},
        {"name": "future", "value": "fresh", "expires": NOW.timestamp() + 60},
    ]
    result = flaresolverr.image_headers(
        {
            "userAgent": "Browser/1",
            "cookies": [
                {**cookie, "domain": "publisher.example", "path": "/images"} for cookie in cookies
            ],
        },
        f"{scheme}://publisher.example/images/logo.png",
    )
    assert result == {
        "User-Agent": "Browser/1",
        "Cookie": ("secure=clearance; " if scheme == "https" else "")
        + "session=current; future=fresh",
    }


def test_provider_json_envelope_is_bounded_before_untrusted_solution_is_parsed(monkeypatch):
    payload = {"status": "ok", "solution": {"status": 200, "url": URL, "response": "x" * 80000}}
    use_pool(monkeypatch, [httpcore.Response(200, content=json.dumps(payload).encode())])
    with pytest.raises(FeedError) as raised:
        flaresolverr.FlareSolverr("http://solver.internal:8191").solve(
            SolveRequest(URL, 10, 1024, "DEVFEED_FEED_MAX_BYTES")
        )
    assert raised.value.reason == "response_too_large" and raised.value.limit_bytes == 1024
    assert raised.value.limit_setting == "DEVFEED_FEED_MAX_BYTES"
