"""Pinned feed connections retain retry budgets and reject incomplete transports."""

import socket
import time
from datetime import timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import Mock

import httpcore
import pytest
from devfeed_core.feeds import fetcher, solvers
from devfeed_core.feeds.fetcher import FeedError
from test_fetcher import address, use_pool
from test_source_readiness_unit import NOW


@pytest.mark.parametrize("recover", [False, True])
def test_pinned_connect_tries_distinct_public_addresses_with_the_remaining_deadline(
    monkeypatch, recover
):
    resolver = Mock(return_value=[address("1.1.1.1"), address("1.1.1.1"), address("8.8.8.8")])
    monkeypatch.setattr(
        fetcher,
        "socket",
        SimpleNamespace(
            getaddrinfo=resolver, SOCK_STREAM=socket.SOCK_STREAM, gaierror=socket.gaierror
        ),
    )
    clock = iter([10, 10, 11])
    monkeypatch.setattr(fetcher, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    last = httpcore.ConnectError("Connection unavailable")
    stream = object()
    connect = Mock(
        side_effect=[httpcore.ConnectTimeout("Connection timed out"), stream if recover else last]
    )
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    if recover:
        assert (
            fetcher.PublicNetworkBackend().connect_tcp("publisher.example", 443, timeout=5)
            is stream
        )
    else:
        with pytest.raises(httpcore.ConnectError) as raised:
            fetcher.PublicNetworkBackend().connect_tcp("publisher.example", 443, timeout=5)
        assert raised.value is last
    assert [call.args[:3] for call in connect.call_args_list] == [
        ("1.1.1.1", 443, 5),
        ("8.8.8.8", 443, 4),
    ]


def test_dns_failure_exposes_a_safe_transport_error_without_resolver_diagnostics(monkeypatch):
    resolver = Mock(side_effect=socket.gaierror("private internal resolver address"))
    monkeypatch.setattr(
        fetcher,
        "socket",
        SimpleNamespace(
            getaddrinfo=resolver, SOCK_STREAM=socket.SOCK_STREAM, gaierror=socket.gaierror
        ),
    )
    connect = Mock()
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    with pytest.raises(httpcore.ConnectError, match="^Feed DNS resolution failed$"):
        fetcher.PublicNetworkBackend().connect_tcp("publisher.example", 443)
    connect.assert_not_called()


@pytest.mark.parametrize("delay,expected", [(timedelta(days=2), 86400), (-timedelta(seconds=1), 0)])
def test_http_date_retry_after_is_bounded_and_expired_dates_do_not_delay_work(
    monkeypatch, delay, expected
):
    monkeypatch.setattr(fetcher, "datetime", SimpleNamespace(now=lambda _: NOW))
    assert fetcher.retry_after_seconds(format_datetime(NOW + delay, usegmt=True)) == expected


@pytest.mark.parametrize("phase", ["before-request", "while-streaming"])
def test_total_download_budget_never_returns_partial_feed_content(monkeypatch, phase):
    pool = use_pool(monkeypatch, [httpcore.Response(200, content=b"partial feed")])
    clock = iter([0, 61] if phase == "before-request" else [0, 0, 61])
    monkeypatch.setattr(
        fetcher,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock), perf_counter=time.perf_counter),
    )
    with pytest.raises(FeedError) as raised:
        fetcher.fetch_feed("https://publisher.example/feed")
    assert raised.value.reason == "deadline_exceeded" and raised.value.retryable
    assert len(pool.requests) == int(phase == "while-streaming")


@pytest.mark.parametrize(
    "response,reason",
    [
        (httpcore.Response(302), "invalid_redirect"),
        (
            httpcore.Response(200, headers={"content-encoding": "br"}, content=b"encoded"),
            "unsupported_encoding",
        ),
    ],
)
def test_incomplete_redirects_and_unsupported_encodings_are_safe_terminal_failures(
    monkeypatch, response, reason
):
    pool = use_pool(monkeypatch, [response])
    with pytest.raises(FeedError) as raised:
        fetcher.fetch_feed("https://publisher.example/feed")
    assert raised.value.reason == reason and not raised.value.retryable
    assert len(pool.requests) == 1


def test_last_modified_validator_is_forwarded_only_to_its_original_origin(monkeypatch):
    pool = use_pool(
        monkeypatch,
        [
            httpcore.Response(302, headers={"location": "https://other.example/feed"}),
            httpcore.Response(304),
        ],
    )
    stamp = format_datetime(NOW, usegmt=True)
    result = fetcher.fetch_feed("https://publisher.example/feed", '"edition"', stamp)
    assert result.status == 304
    assert pool.requests[0][1]["If-Modified-Since"] == stamp
    assert pool.requests[0][1]["If-None-Match"] == '"edition"'
    assert (
        "If-Modified-Since" not in pool.requests[1][1]
        and "If-None-Match" not in pool.requests[1][1]
    )


@pytest.mark.parametrize("reason", ["transport_error", "browser_challenge"])
def test_topic_logo_error_does_not_invoke_an_unconfigured_or_irrelevant_solver(monkeypatch, reason):
    failure = FeedError("Image retrieval could not finish", reason=reason)
    monkeypatch.setattr(fetcher, "_fetch", Mock(side_effect=failure))
    solve = Mock()
    monkeypatch.setattr(solvers, "fetch_solved_page", solve)
    with pytest.raises(FeedError) as raised:
        fetcher.fetch_topic_logo("https://publisher.example/logo.png")
    assert raised.value is failure
    solve.assert_not_called()
