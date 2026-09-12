import json
import socket
from types import SimpleNamespace

import httpcore
import pytest
from devfeed_core.config import SolverService, get_settings
from devfeed_core.feeds import solvers
from devfeed_core.feeds.fetcher import (
    FeedError,
    FetchResult,
    fetch_article_page,
    fetch_page,
    fetch_source_page,
)
from devfeed_core.feeds.solver_providers.flaresolverr import FlareSolverr
from devfeed_core.feeds.solver_types import SolveRequest, SolverUnavailable
from pydantic import ValidationError
from test_fetcher import use_pool

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
