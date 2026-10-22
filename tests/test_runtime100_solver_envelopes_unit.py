"""Isolated solver workers preserve bounded execution and safe result envelopes."""

import base64
import socket
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core.config import get_settings
from devfeed_core.feeds import solvers
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.feeds.solver_types import SolverUnavailable
from test_solvers import PAGE, URL
from test_solvers import services as services
from test_source_readiness_unit import configuration
from test_topic_logo_storage import picture


def test_disabled_solver_refuses_work_before_network_or_coordination(monkeypatch):
    monkeypatch.setattr(solvers, "get_settings", lambda: configuration(solver_services=[]))
    resolver = Mock(side_effect=AssertionError("disabled solver attempted DNS"))
    monkeypatch.setattr(solvers, "socket", SimpleNamespace(getaddrinfo=resolver))
    with pytest.raises(FeedError) as failure:
        solvers.fetch_solved_page(URL, max_bytes=1024, limit_setting="TEST_LIMIT")
    assert failure.value.reason == "browser_challenge"
    resolver.assert_not_called()


def test_solver_dns_failure_is_retryable_without_exposing_resolver_details(monkeypatch, services):
    resolver = Mock(side_effect=socket.gaierror("private resolver endpoint"))
    monkeypatch.setattr(
        solvers,
        "socket",
        SimpleNamespace(
            getaddrinfo=resolver, SOCK_STREAM=socket.SOCK_STREAM, gaierror=socket.gaierror
        ),
    )
    provider = Mock()
    monkeypatch.setattr(solvers, "create_solver", provider)
    with pytest.raises(FeedError) as failure:
        solvers.fetch_solved_page(URL, max_bytes=1024, limit_setting="TEST_LIMIT")
    assert failure.value.reason == "transport_error" and failure.value.retryable
    assert "private resolver" not in str(failure.value)
    provider.assert_not_called()


@pytest.mark.parametrize("page", [replace(PAGE, status=503), replace(PAGE, body=b" \n\t")])
def test_unsuccessful_or_empty_solver_pages_never_become_successful_worker_receipts(
    monkeypatch, services, page
):
    monkeypatch.setattr(
        solvers, "create_solver", lambda _: SimpleNamespace(solve=lambda request: page)
    )
    assert solvers.solve_feed_request(URL) == {"error": "browser_challenge"}
    assert services["released"]


def test_solver_does_not_start_a_second_provider_without_minimum_remaining_budget(
    monkeypatch, services
):
    budget = get_settings().solver_timeout_seconds
    clock = iter([0.0, 0.0, budget - 4.0])
    monkeypatch.setattr(solvers, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    calls = []

    def unavailable(request):
        calls.append(request)
        raise SolverUnavailable(stage="http", http_status=503)

    monkeypatch.setattr(solvers, "create_solver", lambda _: SimpleNamespace(solve=unavailable))
    with pytest.raises(SolverUnavailable) as failure:
        solvers.fetch_solved_page(URL, max_bytes=1024, limit_setting="TEST_LIMIT")
    assert len(calls) == 1 and calls[0].timeout_seconds > 0
    assert failure.value.stage == "http" and failure.value.status == 503
    assert services["released"]


def test_isolated_image_worker_returns_bounded_binary_receipt_without_credentials(
    monkeypatch, services
):
    image = replace(PAGE, body=picture(), content_type="image/png")
    requests = []
    monkeypatch.setattr(
        solvers,
        "create_solver",
        lambda _: SimpleNamespace(solve=lambda request: requests.append(request) or image),
    )
    receipt = solvers.solve_image_request(URL)
    assert receipt == {
        "body": base64.b64encode(image.body).decode("ascii"),
        "url": URL,
        "content_type": "image/png",
    }
    assert len(requests) == 1 and requests[0].image
    assert requests[0].max_bytes == 2_000_000 and requests[0].limit_setting == "topic_logo"
    assert services["released"]


def test_isolated_image_worker_returns_only_safe_failure_reason(monkeypatch, services):
    def denied(request):
        raise FeedError("upstream body contains private credentials", reason="http_error")

    monkeypatch.setattr(solvers, "create_solver", lambda _: SimpleNamespace(solve=denied))
    assert solvers.solve_image_request(URL) == {"error": "http_error"}
    assert services["released"]
