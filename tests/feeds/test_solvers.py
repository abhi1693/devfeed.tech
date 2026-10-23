"""Solver providers preserve bounded payloads, credentials, and queue cleanup."""

import base64
import json
import socket
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import httpcore
import pytest
from devfeed_core.config import SolverService, get_settings
from devfeed_core.feeds import queued_solver, solvers
from devfeed_core.feeds import queued_solver as source_solver
from devfeed_core.feeds.fetcher import (
    FeedError,
    FetchResult,
    fetch_article_page,
    fetch_page,
    fetch_source_page,
)
from devfeed_core.feeds.solver_providers import flaresolverr
from devfeed_core.feeds.solver_providers.flaresolverr import FlareSolverr
from devfeed_core.feeds.solver_types import SolveRequest, SolverUnavailable
from pydantic import ValidationError
from redis.exceptions import ConnectionError
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.exceptions import NoSuchJobError
from rq.job import JobStatus

from tests.discovery.test_source_discovery import feed as feed
from tests.feeds.test_fetcher import use_pool
from tests.images.test_topic_logo_storage import picture
from tests.support.source_readiness import NOW, configuration

URL = "https://publisher.example/article"


PAGE = FetchResult(
    200,
    b'<html><title>Engineering</title><meta property="og:image" content="/a.png"></html>',
    URL,
    content_type="text/html",
)


@pytest.fixture
def services(monkeypatch):
    values = [
        SolverService(provider="flaresolverr", url=f"http://solver-{n}.internal:8191")
        for n in (1, 2)
    ]
    monkeypatch.setattr(get_settings(), "solver_services", values)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    state = {"released": False, "busy": False}
    lock = SimpleNamespace(
        acquire=lambda **k: not state["busy"], release=lambda: state.update(released=True)
    )

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def lock(self, *a, **k):
            return lock

    monkeypatch.setattr(solvers, "create_redis", lambda *a, **k: Client())
    return state


def solve():
    return solvers.fetch_solved_page(URL, max_bytes=1024, limit_setting="TEST_LIMIT")


@pytest.mark.parametrize("fetch", [fetch_article_page, fetch_page, fetch_source_page])
def test_every_html_enrichment_detects_cloudflare_and_uses_configured_solver(
    monkeypatch, services, fetch
):
    use_pool(monkeypatch, [httpcore.Response(403, headers={"cf-mitigated": "challenge"})])
    calls = []
    monkeypatch.setattr(
        solvers,
        "create_solver",
        lambda s: SimpleNamespace(solve=lambda request: calls.append(request) or PAGE),
    )
    assert fetch(URL).body == PAGE.body
    assert len(calls) == 1 and calls[0].url == URL
    assert services["released"]


@pytest.mark.parametrize("fetch", [fetch_article_page, fetch_page, fetch_source_page])
@pytest.mark.parametrize("status", [403, 404, 429, 503])
def test_plain_http_errors_never_invoke_a_solver(monkeypatch, services, fetch, status):
    use_pool(monkeypatch, [httpcore.Response(status)])
    monkeypatch.setattr(solvers, "create_solver", lambda s: pytest.fail("unexpected solver"))
    with pytest.raises(FeedError) as exc:
        fetch(URL)
    assert exc.value.reason == "http_error"


def test_failover_uses_second_service_and_keeps_the_request_budget(monkeypatch, services):
    calls = []

    def provider(service):
        def run(request):
            calls.append((service.url, request.timeout_seconds))
            if len(calls) == 1:
                raise SolverUnavailable()
            return PAGE

        return SimpleNamespace(solve=run)

    monkeypatch.setattr(solvers, "create_solver", provider)
    assert solve() == PAGE
    assert len(calls) == 2 and all(0 < budget <= 30 for _, budget in calls)
    assert services["released"]


def test_uncertain_remote_timeout_keeps_lease_and_does_not_launch_another_solver(
    monkeypatch, services
):
    calls = []

    def run(request):
        calls.append(request)
        raise SolverUnavailable(work_may_continue=True)

    monkeypatch.setattr(solvers, "create_solver", lambda s: SimpleNamespace(solve=run))
    with pytest.raises(SolverUnavailable):
        solve()
    assert len(calls) == 1 and not services["released"]


def test_busy_solver_defers_without_calling_service(monkeypatch, services):
    services["busy"] = True
    monkeypatch.setattr(solvers, "create_solver", lambda s: pytest.fail("unexpected solver"))
    with pytest.raises(FeedError) as exc:
        solve()
    assert exc.value.reason == "solver_busy" and exc.value.retryable


@pytest.mark.parametrize(
    "page,reason",
    [
        (
            FetchResult(200, b"x", "http://169.254.169.254/", content_type="text/html"),
            "private_address",
        ),
        (
            FetchResult(200, b"x", "http://publisher.example/", content_type="text/html"),
            "unsafe_redirect",
        ),
        (FetchResult(200, b"x" * 1025, URL, content_type="text/html"), "response_too_large"),
        (
            FetchResult(200, b"<title>Just a moment...</title>", URL, content_type="text/html"),
            "browser_challenge",
        ),
        (
            FetchResult(
                200,
                b"<title>CAPTCHA</title><div id='aws-waf-captcha-container'></div>",
                URL,
                content_type="text/html",
            ),
            "browser_challenge",
        ),
        (
            FetchResult(200, b"<feed/>", URL, content_type="application/xml"),
            "unsupported_content_type",
        ),
    ],
)
def test_provider_results_share_security_size_and_challenge_validation(
    monkeypatch, services, page, reason
):
    monkeypatch.setattr(solvers, "create_solver", lambda s: SimpleNamespace(solve=lambda r: page))
    with pytest.raises(FeedError) as exc:
        solve()
    assert exc.value.reason == reason


def test_input_dns_rejection_happens_before_solver_request(monkeypatch, services):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))],
    )
    monkeypatch.setattr(solvers, "create_solver", lambda s: pytest.fail("unexpected solver"))
    with pytest.raises(FeedError, match="URL rejected"):
        solve()


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/test",
        "https://user:password@solver.example",
        "http://solver/v1",
        "http://solver/?token=x",
        "http://solver:70000",
    ],
)
def test_service_configuration_rejects_unsafe_or_ambiguous_origins(url):
    with pytest.raises(ValidationError):
        SolverService(provider="flaresolverr", url=url)


def test_adapter_normalizes_protocol_without_retaining_cookies(monkeypatch):
    payload = {
        "status": "ok",
        "solution": {
            "status": 200,
            "url": URL,
            "response": PAGE.body.decode(),
            "cookies": [{"name": "secret", "value": "private"}],
        },
    }
    pool = use_pool(monkeypatch, [httpcore.Response(200, content=json.dumps(payload).encode())])
    result = FlareSolverr("http://solver.internal:8191").solve(
        SolveRequest(URL, 10, 1024, "TEST_LIMIT")
    )
    assert result.body == PAGE.body and not hasattr(result, "cookies")
    assert pool.requests[0][0] == "http://solver.internal:8191/v1"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"status": "error", "message": "secret"},
        {"status": "ok", "solution": {"response": "text"}},
    ],
)
def test_adapter_rejects_invalid_responses_without_leaking_remote_messages(monkeypatch, payload):
    use_pool(monkeypatch, [httpcore.Response(200, content=json.dumps(payload).encode())])
    with pytest.raises(SolverUnavailable) as exc:
        FlareSolverr("http://solver.internal:8191").solve(SolveRequest(URL, 10, 1024, "TEST_LIMIT"))
    assert "secret" not in str(exc.value)


def test_article_discussing_challenges_is_not_itself_a_challenge(monkeypatch, services):
    page = FetchResult(
        200,
        b"<title>Understanding WAF</title>"
        b"<p>Inspect window.awsWafCookieDomainList and challenge-form.</p>",
        URL,
        content_type="text/html",
    )
    monkeypatch.setattr(solvers, "create_solver", lambda s: SimpleNamespace(solve=lambda r: page))
    assert solve() == page


def test_unresolved_aws_script_is_rejected_even_with_empty_title(monkeypatch, services):
    page = FetchResult(
        200,
        b"<title></title><script>window.awsWafCookieDomainList = [];</script>",
        URL,
        content_type="text/html",
    )
    monkeypatch.setattr(solvers, "create_solver", lambda s: SimpleNamespace(solve=lambda r: page))
    with pytest.raises(FeedError) as exc:
        solve()
    assert exc.value.reason == "browser_challenge"


@pytest.mark.parametrize(
    "body",
    [
        b'<rss version="2.0"><channel><title>News</title></channel></rss>',
        (
            b'<html><body><pre>&lt;rss version="2.0"&gt;&lt;channel&gt;'
            b"&lt;title&gt;News&lt;/title&gt;&lt;/channel&gt;&lt;/rss&gt;</pre></body></html>"
        ),
    ],
)
def test_solver_feed_accepts_xml_and_browser_text_wrapper(monkeypatch, services, body):
    import base64
    from datetime import UTC, datetime

    from devfeed_core.feeds.parser import parse_feed

    result = FetchResult(200, body, URL, content_type="application/rss+xml")
    monkeypatch.setattr(solvers, "create_solver", lambda _: SimpleNamespace(solve=lambda _: result))
    solved = solvers.solve_feed_request(URL)
    parsed = parse_feed(
        base64.b64decode(solved["body"]), URL, datetime.now(UTC), source_type="publisher"
    )
    assert parsed.title == "News"


def test_solver_feed_does_not_accept_html_landing_page(monkeypatch, services):
    import base64
    from datetime import UTC, datetime

    from devfeed_core.feeds.parser import parse_feed

    monkeypatch.setattr(solvers, "create_solver", lambda _: SimpleNamespace(solve=lambda _: PAGE))
    solved = solvers.solve_feed_request(URL)
    with pytest.raises(FeedError):
        parse_feed(
            base64.b64decode(solved["body"]), URL, datetime.now(UTC), source_type="publisher"
        )


def test_image_challenge_uses_binary_solver_and_rejects_html(monkeypatch, services):
    from devfeed_core.feeds.fetcher import fetch_topic_logo

    use_pool(monkeypatch, [httpcore.Response(403, headers={"cf-mitigated": "challenge"})])
    seen = []
    image = FetchResult(200, b"png bytes", URL, content_type="image/png")
    monkeypatch.setattr(
        solvers,
        "create_solver",
        lambda service: SimpleNamespace(solve=lambda request: seen.append(request) or image),
    )
    assert fetch_topic_logo(URL).body == image.body
    assert seen[0].image and seen[0].max_bytes == 2_000_000
    monkeypatch.setattr(
        solvers, "create_solver", lambda service: SimpleNamespace(solve=lambda request: PAGE)
    )
    with pytest.raises(FeedError, match="did not return an image"):
        solvers.fetch_solved_page(URL, max_bytes=1024, limit_setting="TEST", image=True)


def test_image_adapter_replays_only_scoped_cookies_and_matching_agent(monkeypatch):
    payload = {
        "status": "ok",
        "solution": {
            "url": URL,
            "status": 200,
            "userAgent": "Browser/123",
            "cookies": [
                {
                    "name": "cf_clearance",
                    "value": "clearance",
                    "domain": "publisher.example",
                    "path": "/",
                    "secure": True,
                },
                {"name": "foreign", "value": "secret", "domain": "other.example", "path": "/"},
                {
                    "name": "private_path",
                    "value": "secret",
                    "domain": "publisher.example",
                    "path": "/private",
                },
            ],
        },
    }
    pool = use_pool(
        monkeypatch,
        [
            httpcore.Response(200, content=json.dumps(payload).encode()),
            httpcore.Response(302, headers={"location": "https://cdn.example/image.png"}),
            httpcore.Response(200, content=b"png bytes", headers={"content-type": "image/png"}),
        ],
    )
    result = FlareSolverr("http://solver.internal:8191").solve(
        SolveRequest(URL, 10, 1024, "TEST", image=True)
    )
    assert result.body == b"png bytes"
    assert pool.requests[1][1]["Cookie"] == "cf_clearance=clearance"
    assert pool.requests[1][1]["User-Agent"] == "Browser/123"
    assert "Cookie" not in pool.requests[2][1]
    assert pool.requests[2][1]["User-Agent"] != "Browser/123"
    assert not hasattr(result, "cookies")


@pytest.mark.parametrize(
    "change",
    [
        {"userAgent": "evil\r\nHeader: true"},
        {
            "cookies": [
                {
                    "name": "cf_clearance",
                    "value": "bad\nvalue",
                    "domain": "publisher.example",
                    "path": "/",
                }
            ]
        },
    ],
)
def test_image_cookie_replay_rejects_header_injection(change):
    from devfeed_core.feeds.solver_providers.flaresolverr import image_headers

    with pytest.raises(SolverUnavailable):
        image_headers({"userAgent": "Browser", "cookies": [], **change}, URL)


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_solver_http_failures_retain_only_safe_stage_and_status(
    monkeypatch, services, caplog, status
):
    use_pool(
        monkeypatch,
        [httpcore.Response(status, content=b"private-token internal-url") for _ in range(2)],
    )
    with caplog.at_level("INFO"), pytest.raises(SolverUnavailable) as failure:
        solve()
    assert failure.value.stage == "http" and failure.value.status == status
    assert failure.value.retryable and not failure.value.work_may_continue
    attempts = [record for record in caplog.records if record.msg == "solver_attempt_unavailable"]
    assert len(attempts) == 2
    assert all(
        record.solver_stage == "http" and record.solver_http_status == status for record in attempts
    )
    assert "private-token" not in caplog.text and "internal-url" not in caplog.text


def test_solver_transport_stage_retains_work_may_continue_cleanup(monkeypatch, services, caplog):
    def unavailable(_request):
        raise SolverUnavailable(stage="transport", work_may_continue=True)

    monkeypatch.setattr(solvers, "create_solver", lambda _: SimpleNamespace(solve=unavailable))
    with caplog.at_level("INFO"), pytest.raises(SolverUnavailable) as failure:
        solve()
    assert failure.value.stage == "transport" and failure.value.work_may_continue
    assert not services["released"]
    assert (
        len([record for record in caplog.records if record.msg == "solver_attempt_unavailable"])
        == 1
    )


@pytest.mark.parametrize(
    ("error", "stage", "work_may_continue"),
    [
        (httpcore.ConnectError, "connect", False),
        (httpcore.ConnectTimeout, "connect", False),
        (httpcore.ReadError, "transport", True),
        (httpcore.ReadTimeout, "transport", True),
        (httpcore.RemoteProtocolError, "transport", True),
    ],
)
def test_adapter_classifies_transport_failures(monkeypatch, error, stage, work_may_continue):
    pool = use_pool(monkeypatch, [])

    def fail(*args, **kwargs):
        raise error("private upstream detail")

    monkeypatch.setattr(pool, "stream", fail)
    with pytest.raises(SolverUnavailable) as failure:
        FlareSolverr("http://solver.internal:8191").solve(SolveRequest(URL, 10, 1024, "TEST"))
    assert failure.value.stage == stage
    assert failure.value.work_may_continue is work_may_continue
    assert failure.value.status is None
    assert failure.value.retryable
    assert "private" not in str(failure.value)


@pytest.mark.parametrize("image", [False, True])
def test_adapter_classifies_deadlines_before_return_or_image_replay(monkeypatch, image):
    from devfeed_core.feeds.solver_providers import flaresolverr

    payload = {"status": "ok", "solution": {"status": 200, "url": URL}}
    use_pool(monkeypatch, [httpcore.Response(200, content=json.dumps(payload).encode())])
    clock = iter([0, 1, 11] if image else [0, 16])
    monkeypatch.setattr(flaresolverr, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr(flaresolverr, "_fetch", lambda *a, **k: pytest.fail("Expired replay"))
    with pytest.raises(SolverUnavailable) as failure:
        FlareSolverr("http://solver.internal:8191").solve(
            SolveRequest(URL, 10, 1024, "TEST", image=image)
        )
    assert failure.value.stage == "deadline"
    assert failure.value.work_may_continue is (not image)
    assert failure.value.status is None


@pytest.mark.parametrize("content", [b"not JSON", b"\xff", b'{"status":"ok","solution":null}'])
def test_adapter_classifies_malformed_payloads(monkeypatch, content):
    use_pool(monkeypatch, [httpcore.Response(200, content=content)])
    with pytest.raises(SolverUnavailable) as failure:
        FlareSolverr("http://solver.internal:8191").solve(SolveRequest(URL, 10, 1024, "TEST"))
    assert failure.value.stage == "invalid_response"
    assert not failure.value.work_may_continue
    assert failure.value.status is None


@pytest.mark.parametrize(
    "cookies",
    [
        None,
        [{}] * 101,
        [
            {"name": f"cookie{i}", "value": "x" * 4096, "domain": "publisher.example"}
            for i in range(3)
        ],
    ],
)
def test_image_cookie_limits_produce_safe_invalid_response_stage(cookies):
    from devfeed_core.feeds.solver_providers.flaresolverr import image_headers

    with pytest.raises(SolverUnavailable) as failure:
        image_headers({"userAgent": "Browser", "cookies": cookies}, URL)
    assert failure.value.stage == "invalid_response"
    assert failure.value.status is None
    assert str(failure.value) == "Solver service unavailable"


def test_redis_outage_has_coordination_stage_without_claiming_remote_work(monkeypatch, services):
    from redis.exceptions import RedisError

    def unavailable(*args, **kwargs):
        raise RedisError("private redis endpoint")

    monkeypatch.setattr(solvers, "create_redis", unavailable)
    with pytest.raises(SolverUnavailable) as failure:
        solve()
    assert failure.value.stage == "coordination"
    assert not failure.value.work_may_continue
    assert failure.value.status is None
    assert "private" not in str(failure.value)


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


@pytest.mark.parametrize("size", [1025, 1030])
def test_queued_solver_rejects_oversized_encoded_and_decoded_receipts(queued, monkeypatch, size):
    monkeypatch.setattr(get_settings(), "feed_max_bytes", 1024)
    queued.job.return_value.return_value = {
        "url": FEED_URL,
        "body": base64.b64encode(b"a" * size).decode(),
    }
    with pytest.raises(FeedError) as raised:
        queued_solver.solve_feed(FEED_URL)
    assert raised.value.reason == "response_too_large"
    queued.job.delete.assert_called_once_with()


@pytest.mark.parametrize("status", [JobStatus.FAILED, JobStatus.CANCELED, JobStatus.STOPPED])
def test_terminal_solver_work_returns_a_safe_retry_without_reading_a_partial_result(queued, status):
    queued.job.get_status.return_value = status
    with pytest.raises(FeedError) as raised:
        queued_solver.solve_feed(FEED_URL)
    assert raised.value.reason == "solver_unavailable" and raised.value.retryable
    assert raised.value.retry_after == 60
    queued.job.return_value.assert_not_called()
    queued.job.delete.assert_called_once_with()
    queued.job.cancel.assert_not_called()


def test_job_expiry_during_polling_is_a_safe_retry_and_cleanup_does_not_mask_it(queued):
    queued.job.get_status.side_effect = NoSuchJobError("private work receipt")
    with pytest.raises(FeedError) as raised:
        queued_solver.solve_feed(FEED_URL)
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
    result = queued_solver.solve_feed(FEED_URL)
    assert result.body == b"feed" and result.final_url == FEED_URL
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
    payload = {
        "status": "ok",
        "solution": {"status": 200, "url": FEED_URL, "response": "x" * 80000},
    }
    use_pool(monkeypatch, [httpcore.Response(200, content=json.dumps(payload).encode())])
    with pytest.raises(FeedError) as raised:
        flaresolverr.FlareSolverr("http://solver.internal:8191").solve(
            SolveRequest(FEED_URL, 10, 1024, "DEVFEED_FEED_MAX_BYTES")
        )
    assert raised.value.reason == "response_too_large" and raised.value.limit_bytes == 1024
    assert raised.value.limit_setting == "DEVFEED_FEED_MAX_BYTES"


FEED_URL = "https://publication.example/feed"


@pytest.fixture
def queued(monkeypatch):
    monkeypatch.setattr(get_settings(), "solver_queue_enabled", True)
    job = Mock()
    job.get_status.return_value = JobStatus.FINISHED
    job.return_value.return_value = {"body": base64.b64encode(b"feed").decode(), "url": FEED_URL}
    queue = Mock()
    queue.enqueue.return_value = job
    monkeypatch.setattr(source_solver, "create_redis", lambda *a, **kw: nullcontext(object()))
    monkeypatch.setattr(source_solver, "Queue", lambda *a, **kw: queue)
    return SimpleNamespace(job=job, queue=queue)


def test_explicit_request_uses_fixed_worker_target_and_expires(queued):
    result = source_solver.solve_feed(FEED_URL)
    assert result.body == b"feed"
    args, kwargs = queued.queue.enqueue.call_args
    assert args == ("devfeed_core.feeds.solvers.solve_feed_request", FEED_URL)
    assert kwargs["ttl"] == 15
    assert kwargs["job_timeout"] == 80
    assert kwargs["result_ttl"] == 120
    queued.job.delete.assert_called_once()


def test_timeout_cancels_queued_request(queued, monkeypatch):
    queued.job.get_status.return_value = JobStatus.QUEUED
    monkeypatch.setattr(source_solver.time, "monotonic", Mock(side_effect=[0, 96]))
    with pytest.raises(FeedError, match="did not finish"):
        source_solver.solve_feed(FEED_URL)
    queued.job.cancel.assert_called_once()
    queued.job.delete.assert_called_once()


def test_timeout_leaves_running_work_to_its_bounded_worker_timeout(queued, monkeypatch):
    queued.job.get_status.return_value = JobStatus.STARTED
    monkeypatch.setattr(source_solver.time, "monotonic", Mock(side_effect=[0, 96]))
    with pytest.raises(FeedError):
        source_solver.solve_feed(FEED_URL)
    queued.job.cancel.assert_not_called()
    queued.job.delete.assert_not_called()


def test_solver_errors_do_not_expose_worker_internals(queued):
    queued.job.return_value.return_value = {"error": "internal provider detail"}
    with pytest.raises(FeedError) as error:
        source_solver.solve_feed(FEED_URL)
    assert error.value.reason == "solver_unavailable"
    assert "internal" not in str(error.value)


def test_redis_failure_is_actionable(queued):
    queued.queue.enqueue.side_effect = ConnectionError("private connection details")
    with pytest.raises(FeedError, match="solver is unavailable") as error:
        source_solver.solve_feed(FEED_URL)
    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "result",
    [{}, {"body": "!", "url": FEED_URL}, {"body": [], "url": FEED_URL}, {"body": "", "url": None}],
)
def test_invalid_solver_result_is_safe_and_cleaned_up(queued, result):
    queued.job.return_value.return_value = result
    with pytest.raises(FeedError) as error:
        source_solver.solve_image(FEED_URL)
    assert error.value.reason == "solver_unavailable"
    queued.job.delete.assert_called_once()


def test_disabled_solver_does_not_enqueue(queued, monkeypatch):
    monkeypatch.setattr(get_settings(), "solver_queue_enabled", False)
    with pytest.raises(FeedError, match="not enabled"):
        source_solver.solve_feed(FEED_URL)
    queued.queue.enqueue.assert_not_called()


def test_queued_image_returns_binary_and_uses_image_worker_target(queued):
    queued.job.return_value.return_value = {
        "body": base64.b64encode(b"\x89PNG\xff\x00").decode(),
        "url": FEED_URL,
        "content_type": "image/png",
    }
    result = source_solver.solve_image(FEED_URL)
    assert result.body == b"\x89PNG\xff\x00"
    assert result.content_type == "image/png"
    assert queued.queue.enqueue.call_args.args == (
        "devfeed_core.feeds.solvers.solve_image_request",
        FEED_URL,
    )


def test_image_fetch_hands_challenges_to_isolated_solver(monkeypatch, queued):
    from devfeed_core.feeds import fetcher

    def challenge(*args, **kwargs):
        raise FeedError("Challenge", reason="browser_challenge")

    monkeypatch.setattr(fetcher, "_fetch", challenge)
    queued.job.return_value.return_value = {
        "body": base64.b64encode(b"png bytes").decode(),
        "url": FEED_URL,
        "content_type": "image/png",
    }
    assert fetcher.fetch_topic_logo(FEED_URL).body == b"png bytes"
    queued.queue.enqueue.assert_called_once()


def test_queued_solver_polls_unfinished_work_before_consuming_the_complete_feed_receipt(
    monkeypatch, queued, feed
):
    queued.job.get_status.side_effect = [JobStatus.STARTED, JobStatus.FINISHED] + [
        JobStatus.FINISHED
    ] * 2
    queued.job.return_value.return_value = {
        "body": base64.b64encode(feed).decode(),
        "url": FEED_URL,
    }
    delays = []
    clock = [100.0]

    def sleep(delay):
        delays.append(delay)
        clock[0] += delay

    monkeypatch.setattr(
        queued_solver, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)
    )

    result = queued_solver.solve_feed(FEED_URL)

    assert result.body == feed and result.final_url == FEED_URL
    assert len(delays) == 1 and 0 < delays[0] <= 1
    queued.job.cancel.assert_not_called()
    queued.job.delete.assert_called_once_with()


@pytest.mark.parametrize("status", [403, True])
def test_successful_solver_http_envelope_cannot_hide_an_unsolved_or_untyped_page_status(
    monkeypatch, status
):
    content = json.dumps(
        {
            "status": "ok",
            "solution": {"url": FEED_URL, "response": "<p>Denied</p>", "status": status},
        }
    ).encode()
    use_pool(monkeypatch, [httpcore.Response(200, content=content)])
    provider = flaresolverr.FlareSolverr("http://solver.internal:8191")

    with pytest.raises(FeedError) as failure:
        provider.solve(SolveRequest(FEED_URL, 10, 1024, "feed"))

    assert failure.value.reason == "browser_challenge"


def test_malformed_cookie_items_are_ignored_without_losing_a_valid_scoped_cookie():
    headers = flaresolverr.image_headers(
        {
            "userAgent": "Browser/1.0",
            "cookies": [
                None,
                "untrusted scalar",
                {
                    "name": "session",
                    "value": "allowed",
                    "domain": "publication.example",
                    "path": "/",
                },
            ],
        },
        FEED_URL,
    )
    assert headers == {"User-Agent": "Browser/1.0", "Cookie": "session=allowed"}
