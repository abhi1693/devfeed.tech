"""Feed transport validates public destinations, payloads, and response budgets."""

import gzip
import socket
import time
from contextlib import contextmanager
from datetime import timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import Mock

import httpcore
import pytest
from devfeed_core import source_inspection, source_relevance
from devfeed_core.feeds import fetcher, parser, solvers, validation
from devfeed_core.feeds.fetcher import (
    FeedError,
    ImmediateRedirect,
    PublicNetworkBackend,
    fetch_article_page,
    fetch_feed,
    fetch_page,
    fetch_source_page,
    retry_after_seconds,
)

from tests.support.source_readiness import NOW, configuration, profile, source

FEED_URL = "https://publication.example/feed"


@pytest.mark.parametrize(
    "content,target",
    [
        (" 0.000 ; URL = '/new' \n", "/new"),
        ('0;url="https://publisher.example/new?a=1;b=2"', "https://publisher.example/new?a=1;b=2"),
        ("0;url=/new\t", "/new"),
        ("5;url=/new", None),
        ("0.1;url=/new", None),
        ("0.;url=/new", None),
        ("0;url=/first\n/second", None),
        ("0;url=   ", None),
    ],
)
def test_immediate_refresh_preserves_delay_quotes_and_target_rules(content, target):
    parser = ImmediateRedirect()
    parser.handle_starttag("meta", [("http-equiv", "refresh"), ("content", content)])
    assert parser.target == target


def test_empty_refresh_does_not_mask_a_later_valid_target():
    parser = ImmediateRedirect()
    parser.feed(
        '<meta http-equiv="refresh" content="0;url=   ">'
        '<meta http-equiv="refresh" content="0;url=/new">'
    )
    assert parser.target == "/new"


def address(ip):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))


def test_dns_guard_checks_all_addresses(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: [address("93.184.216.34"), address("10.0.0.1")]
    )
    with pytest.raises(FeedError, match="private"):
        PublicNetworkBackend().connect_tcp("example.com", 443)


def test_dns_rebinding_guard_connects_to_validated_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [address("93.184.216.34")])
    connected = []
    monkeypatch.setattr(
        httpcore.SyncBackend,
        "connect_tcp",
        lambda self, host, *a: connected.append(host) or "stream",
    )
    assert PublicNetworkBackend().connect_tcp("example.com", 443) == "stream"
    assert connected == ["93.184.216.34"]


class FakePool:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @contextmanager
    def stream(self, method, url, **kwargs):
        # Match the real transport's URL validation; a permissive fake previously
        # hid Unicode URLs rejected by httpcore before opening a connection.
        httpcore.URL(url)
        self.requests.append((url, dict(kwargs["headers"])))
        yield next(self.responses)


def use_pool(monkeypatch, responses):
    pool = FakePool(responses)
    monkeypatch.setattr(httpcore, "ConnectionPool", lambda **kwargs: pool)
    return pool


def test_conditional_fetch_and_cross_origin_redirect_headers(monkeypatch):
    pool = use_pool(
        monkeypatch,
        [
            httpcore.Response(302, headers={"location": "https://elsewhere.example/rss"}),
            httpcore.Response(304),
        ],
    )
    result = fetch_feed("https://example.com/rss", '"test-etag"')
    assert result.status == 304
    assert pool.requests[0][1]["If-None-Match"] == '"test-etag"'
    assert "If-None-Match" not in pool.requests[1][1]


@pytest.mark.parametrize("fetch", [fetch_feed, fetch_page, fetch_article_page, fetch_source_page])
def test_redirect_cannot_reach_metadata_service(monkeypatch, fetch):
    use_pool(monkeypatch, [httpcore.Response(302, headers={"location": "http://169.254.169.254/"})])
    with pytest.raises(FeedError, match="rejected"):
        fetch("https://example.com/rss")


def test_retry_after_and_status_classification(monkeypatch):
    use_pool(monkeypatch, [httpcore.Response(429, headers={"retry-after": "120"})])
    with pytest.raises(FeedError) as result:
        fetch_feed("https://example.com/rss")
    assert result.value.retryable is True
    assert result.value.retry_after == 120
    assert retry_after_seconds("garbage") == 0


@pytest.mark.parametrize("fetch", [fetch_feed, fetch_page, fetch_article_page, fetch_source_page])
@pytest.mark.parametrize("status,action", [(202, "challenge"), (405, "captcha")])
def test_publisher_browser_challenges_are_explained_without_retrying(
    monkeypatch, fetch, status, action
):
    pool = use_pool(
        monkeypatch,
        [httpcore.Response(status, headers={"X-Amzn-Waf-Action": action}, content=b"")],
    )
    with pytest.raises(FeedError, match="publisher requires browser verification") as error:
        fetch("https://example.com/rss")
    assert error.value.reason == "browser_challenge"
    assert error.value.status == status
    assert error.value.retryable is False
    assert len(pool.requests) == 1


def test_202_alone_does_not_imply_browser_verification(monkeypatch):
    use_pool(monkeypatch, [httpcore.Response(202, content=b"")])
    with pytest.raises(FeedError, match="Feed returned HTTP 202") as error:
        fetch_feed("https://example.com/rss")
    assert error.value.reason == "http_error"
    assert retry_after_seconds("999999") == 86400


def test_feed_size_limit_is_enforced_while_streaming(monkeypatch):
    from devfeed_core.config import get_settings

    monkeypatch.setattr(get_settings(), "feed_max_bytes", 4)
    use_pool(monkeypatch, [httpcore.Response(200, content=b"12345")])
    with pytest.raises(FeedError, match="maximum response size"):
        fetch_feed("https://example.com/rss")


def test_gzip_is_decoded_and_expansion_is_bounded(monkeypatch):
    from devfeed_core.config import get_settings

    payload = b"<rss>small</rss>"
    use_pool(
        monkeypatch,
        [
            httpcore.Response(
                200, headers={"content-encoding": "gzip"}, content=gzip.compress(payload)
            )
        ],
    )
    assert fetch_feed("https://example.com/rss").body == payload
    monkeypatch.setattr(get_settings(), "feed_max_bytes", 100)
    use_pool(
        monkeypatch,
        [
            httpcore.Response(
                200, headers={"content-encoding": "gzip"}, content=gzip.compress(b"x" * 100_000)
            )
        ],
    )
    with pytest.raises(FeedError, match="maximum response size"):
        fetch_feed("https://example.com/rss")


@pytest.mark.parametrize("fetch", [fetch_feed, fetch_page, fetch_article_page, fetch_source_page])
def test_truncated_gzip_is_not_imported(monkeypatch, fetch):
    use_pool(
        monkeypatch,
        [
            httpcore.Response(
                200, headers={"content-encoding": "gzip"}, content=gzip.compress(b"test")[:-5]
            )
        ],
    )
    with pytest.raises(FeedError, match="truncated"):
        fetch("https://example.com/rss")


@pytest.mark.parametrize("fetch", [fetch_feed, fetch_page, fetch_article_page, fetch_source_page])
@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://example.com/日本語?q=東京&raw=%2f+%20&x=1&x=2",
            "https://example.com/%E6%97%A5%E6%9C%AC%E8%AA%9E"
            "?q=%E6%9D%B1%E4%BA%AC&raw=%2f+%20&x=1&x=2",
        ),
        (
            "https://münich.example/café?q=naïve&signature=a%2Fb%3D",
            "https://xn--mnich-kva.example/caf%C3%A9?q=na%C3%AFve&signature=a%2Fb%3D",
        ),
        (
            "https://example.com/%E6%97%A5?q=%e6%97%a5&sig=a+b%2B%25&empty=",
            "https://example.com/%E6%97%A5?q=%e6%97%a5&sig=a+b%2B%25&empty=",
        ),
        (
            "https://example.com/a:@!$&'()*+,;=-._~?a[]=1&raw=:/?@!$'()*+,;=",
            "https://example.com/a:@!$&'()*+,;=-._~?a[]=1&raw=:/?@!$'()*+,;=",
        ),
    ],
)
def test_unicode_urls_are_transport_encoded_without_double_escaping(
    monkeypatch, fetch, url, expected
):
    from devfeed_core.urls import validate_public_url

    pool = use_pool(monkeypatch, [httpcore.Response(200, content=b"content")])
    result = fetch(url)
    assert pool.requests[0][0] == expected
    assert httpcore.URL(expected).target.isascii()
    assert result.final_url == validate_public_url(url)  # No change to stored identity.


@pytest.mark.parametrize("fetch", [fetch_feed, fetch_page, fetch_article_page, fetch_source_page])
def test_relative_redirect_retains_unicode_base_and_percent_encoded_query(monkeypatch, fetch):
    pool = use_pool(
        monkeypatch,
        [
            httpcore.Response(302, headers={"location": "next?term=%E6%97%A5&sig=a%2fb+%20"}),
            httpcore.Response(200, content=b"content"),
        ],
    )
    result = fetch("https://example.com/日本語/start")
    assert pool.requests[1][0] == (
        "https://example.com/%E6%97%A5%E6%9C%AC%E8%AA%9E/next?term=%E6%97%A5&sig=a%2fb+%20"
    )
    assert result.final_url == "https://example.com/日本語/next?term=%E6%97%A5&sig=a%2fb+%20"


@pytest.mark.parametrize("encoding", ["identity", "gzip"])
def test_article_budget_does_not_expand_metadata_or_feed_fetches(monkeypatch, encoding):
    from devfeed_core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "page_max_bytes", 100)
    monkeypatch.setattr(settings, "feed_max_bytes", 100)
    monkeypatch.setattr(settings, "article_page_max_bytes", 2000)
    payload = b"<html><body>" + b"x" * 1000 + b"</body></html>"
    wire = gzip.compress(payload) if encoding == "gzip" else payload
    for fetch in (fetch_article_page, fetch_page, fetch_feed):
        use_pool(
            monkeypatch,
            [httpcore.Response(200, headers={"content-encoding": encoding}, content=wire)],
        )
        if fetch is fetch_article_page:
            assert fetch("https://example.com/article").body == payload
        else:
            with pytest.raises(FeedError) as error:
                fetch("https://example.com/article")
            assert error.value.reason == "response_too_large"
            assert error.value.limit_bytes == 100


@pytest.mark.parametrize("encoding", ["identity", "gzip"])
@pytest.mark.parametrize(
    "fetch,setting",
    [(fetch_article_page, "article_page_max_bytes"), (fetch_source_page, "source_page_max_bytes")],
)
def test_page_limit_stops_stream_and_never_returns_partial_html(
    monkeypatch, encoding, fetch, setting
):
    from devfeed_core.config import get_settings

    monkeypatch.setattr(get_settings(), setting, 1000)
    payload = b"<html>" + b"x" * 100_000

    def body():
        yield gzip.compress(payload) if encoding == "gzip" else payload[:1001]
        pytest.fail("Read more bytes after the page budget was exceeded")

    use_pool(
        monkeypatch,
        [httpcore.Response(200, headers={"content-encoding": encoding}, content=body())],
    )
    with pytest.raises(FeedError) as error:
        fetch("https://example.com/page")
    assert error.value.reason == "response_too_large"
    assert error.value.limit_bytes == 1000 and error.value.status == 200
    assert error.value.limit_setting == f"DEVFEED_{setting.upper()}"
    assert not error.value.retryable


@pytest.mark.parametrize(
    "fetch,setting",
    [(fetch_article_page, "article_page_max_bytes"), (fetch_source_page, "source_page_max_bytes")],
)
def test_page_wire_limit_applies_even_when_decoded_body_fits(monkeypatch, fetch, setting):
    from devfeed_core.config import get_settings

    payload = b"<html>Small article</html>"
    wire = gzip.compress(payload)
    assert len(payload) < len(wire)
    monkeypatch.setattr(get_settings(), setting, len(payload))
    use_pool(
        monkeypatch,
        [httpcore.Response(200, headers={"content-encoding": "gzip"}, content=wire)],
    )
    with pytest.raises(FeedError) as error:
        fetch("https://example.com/page")
    assert error.value.reason == "response_too_large"


@pytest.mark.parametrize("fetch", [fetch_article_page, fetch_source_page])
def test_page_fetch_rejects_non_html_before_reading(monkeypatch, fetch):
    def body():
        pytest.fail("Read a non-HTML page body")
        yield b""

    use_pool(
        monkeypatch,
        [httpcore.Response(200, headers={"content-type": "application/pdf"}, content=body())],
    )
    with pytest.raises(FeedError) as error:
        fetch("https://example.com/page")
    assert error.value.reason == "unsupported_content_type"


def test_evidence_conditional_fetch_requires_validator_and_keeps_ssrf_guards(monkeypatch):
    from devfeed_core.feeds.fetcher import fetch_evidence_page

    pool = use_pool(monkeypatch, [httpcore.Response(304)])
    result = fetch_evidence_page("https://example.com/docs", 5, etag='"v1"')
    assert result.status == 304
    assert pool.requests[0][1]["If-None-Match"] == '"v1"'
    use_pool(monkeypatch, [httpcore.Response(304)])
    with pytest.raises(FeedError):
        fetch_evidence_page("https://example.com/docs", 5)
    use_pool(monkeypatch, [httpcore.Response(302, headers={"location": "http://169.254.169.254/"})])
    with pytest.raises(FeedError):
        fetch_evidence_page("https://example.com/docs", 5, etag='"v1"')


def test_article_follows_immediate_html_redirect_with_http_guards(monkeypatch):
    pool = use_pool(
        monkeypatch,
        [
            httpcore.Response(302, headers={"location": "https://other.example/old"}),
            httpcore.Response(200, content=b'<meta http-equiv="refresh" content="0;url=/new">'),
            httpcore.Response(200, content=b"<article>Readable publisher text</article>"),
        ],
    )
    result = fetch_article_page("https://example.com/old")
    assert result.final_url == "https://other.example/new"
    assert len(pool.requests) == 3
    assert b"Readable publisher text" in result.body


@pytest.mark.parametrize("target", ["https://127.0.0.1/private", "file:///etc/passwd"])
def test_html_redirect_rejects_unsafe_target_before_request(monkeypatch, target):
    pool = use_pool(
        monkeypatch,
        [
            httpcore.Response(
                200, content=f'<meta http-equiv="refresh" content="0;url={target}">'.encode()
            )
        ],
    )
    with pytest.raises(FeedError):
        fetch_article_page("https://example.com/old")
    assert len(pool.requests) == 1


def test_html_redirect_loop_shares_http_hop_budget(monkeypatch):
    pool = use_pool(
        monkeypatch,
        [
            httpcore.Response(200, content=b'<meta http-equiv="refresh" content="0;url=/old">')
            for _ in range(6)
        ],
    )
    with pytest.raises(FeedError) as error:
        fetch_article_page("https://example.com/old")
    assert error.value.reason == "too_many_redirects"
    assert len(pool.requests) == 6


def test_delayed_refresh_and_feed_markup_do_not_redirect(monkeypatch):
    body = b'<meta http-equiv="refresh" content="5;url=/new"><p>Article text</p>'
    pool = use_pool(monkeypatch, [httpcore.Response(200, content=body)])
    assert fetch_article_page("https://example.com/old").body == body
    assert len(pool.requests) == 1
    body = b'<meta http-equiv="refresh" content="0;url=/new">'
    pool = use_pool(monkeypatch, [httpcore.Response(200, content=body)])
    assert fetch_feed("https://example.com/feed").body == body
    assert len(pool.requests) == 1


def test_discovery_hook_covers_every_redirect(monkeypatch):
    from devfeed_core.feeds.fetcher import _fetch

    pool = use_pool(
        monkeypatch,
        [
            httpcore.Response(302, headers={"location": "https://elsewhere.example/rss"}),
            httpcore.Response(200, content=b"<rss/>"),
        ],
    )
    checked = []
    _fetch(
        "https://example.com/rss",
        None,
        None,
        accept="*/*",
        max_bytes=1000,
        timeout=10,
        before_request=checked.append,
    )
    assert checked == [request[0] for request in pool.requests]
    assert len(checked) == 2


def test_discovery_deadline_stops_before_network(monkeypatch):
    from devfeed_core.feeds.fetcher import _fetch

    pool = use_pool(monkeypatch, [])
    with pytest.raises(FeedError) as error:
        _fetch(
            "https://example.com/rss",
            None,
            None,
            accept="*/*",
            max_bytes=1000,
            timeout=10,
            deadline=0,
        )
    assert error.value.reason == "discovery_budget"
    assert not pool.requests


def test_page_redirect_upgrades_old_http_link_without_plaintext_request(monkeypatch):
    pool = use_pool(
        monkeypatch,
        [
            httpcore.Response(301, headers={b"location": b"http://new.example.com:80/article"}),
            httpcore.Response(
                200, headers={b"content-type": b"text/html"}, content=b"<p>Article</p>"
            ),
        ],
    )
    assert (
        fetch_article_page("https://old.example.com/article").final_url
        == "https://new.example.com/article"
    )
    assert all(url.startswith("https:") for url, _ in pool.requests)


def test_feed_downgrade_and_private_page_redirect_still_rejected(monkeypatch):
    from devfeed_core.feeds.fetcher import redirect_target

    with pytest.raises(FeedError, match="HTTPS to HTTP"):
        redirect_target("https://example.com/feed", "http://example.com/feed", html_only=False)
    with pytest.raises(ValueError):
        redirect_target("https://example.com/page", "http://127.0.0.1/page", html_only=True)


def test_failed_upgraded_page_does_not_fall_back_to_http(monkeypatch):
    pool = use_pool(
        monkeypatch,
        [
            httpcore.Response(301, headers={"location": "http://new.example.com/article"}),
            httpcore.Response(503),
        ],
    )
    with pytest.raises(FeedError) as error:
        fetch_article_page("https://old.example.com/article")
    assert error.value.status == 503
    assert [url for url, _ in pool.requests] == [
        "https://old.example.com/article",
        "https://new.example.com/article",
    ]


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


def test_real_feed_parser_skips_unsafe_and_missing_media_urls_before_a_valid_enclosure():
    body = b"""<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
    <channel><title>Engineering</title><item><title>Database indexing</title>
    <link>https://publisher.example/database-indexing</link>
    <media:thumbnail url="http://127.0.0.1/private.png"/><media:thumbnail/>
    <enclosure url="https://publisher.example/cover.png" type="image/png"/>
    </item></channel></rss>"""

    result = parser.parse_feed(body, "https://publisher.example/feed", NOW, source_type="publisher")

    assert result.seen == 1 and result.skipped == 0
    assert result.entries[0].image_url == "https://publisher.example/cover.png"
    assert result.entries[0].source_metadata["image_url"] == result.entries[0].image_url


def test_unexpected_transport_failure_is_logged_and_propagated_without_solver_or_admission(
    monkeypatch, caplog
):
    failure = RuntimeError("transport adapter failed unexpectedly")
    monkeypatch.setattr(validation, "fetch_feed", Mock(side_effect=failure))
    solver = Mock()
    with pytest.raises(RuntimeError) as raised:
        validation.validate_feed(FEED_URL, source_type="publisher", solver=solver)
    assert raised.value is failure
    assert any(
        record.message == "feed_validation_error" and record.exc_info for record in caplog.records
    )
    solver.assert_not_called()


@pytest.mark.parametrize("missing_website", [False, True])
def test_complete_or_unlocated_source_profile_never_fetches_an_unnecessary_page(missing_website):
    values = {
        "description": "A software engineering publication.",
        "website_url": "https://publisher.example/",
        "logo_url": "https://publisher.example/logo.png",
        "image_url": "https://publisher.example/preview.png",
        "language": "en",
    }
    if missing_website:
        values = {"description": values["description"]}
    fetch = Mock(side_effect=AssertionError("unnecessary website lookup"))

    result = source_inspection.complete_website_profile(
        values, None if missing_website else values["website_url"], fetch=fetch
    )

    assert result["description"] == values["description"]
    assert result["website_url"] == values.get("website_url")
    assert result["logo_url"] == values.get("logo_url")
    fetch.assert_not_called()


@pytest.mark.parametrize("mode", ["automatic", "manual", "paused-ai", "undiscovered"])
def test_legacy_source_routing_requires_current_automation_ai_and_a_discovered_feed(
    monkeypatch, mode
):
    settings = configuration(ai_enabled=True, full_automation=mode != "manual")
    if mode == "paused-ai":
        settings.ai_enabled = False
    monkeypatch.setattr(source_relevance, "get_settings", lambda: settings)
    current = source(feed_url=None if mode == "undiscovered" else FEED_URL)
    legacy = profile(current, status="queued", operation="legacy", result={})

    assert source_relevance.requires_relevance(current, legacy) is (mode == "automatic")
