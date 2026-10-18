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
