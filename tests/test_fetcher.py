import gzip
import socket
from contextlib import contextmanager

import httpcore
import pytest
from devfeed_core.feeds.fetcher import (
    FeedError,
    PublicNetworkBackend,
    fetch_article_page,
    fetch_feed,
    fetch_page,
    retry_after_seconds,
)


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


@pytest.mark.parametrize("fetch", [fetch_feed, fetch_page, fetch_article_page])
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


@pytest.mark.parametrize("fetch", [fetch_feed, fetch_page, fetch_article_page])
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


@pytest.mark.parametrize("fetch", [fetch_feed, fetch_page, fetch_article_page])
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


@pytest.mark.parametrize("fetch", [fetch_feed, fetch_page, fetch_article_page])
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
def test_article_limit_stops_stream_and_never_returns_partial_html(monkeypatch, encoding):
    from devfeed_core.config import get_settings

    monkeypatch.setattr(get_settings(), "article_page_max_bytes", 1000)
    payload = b"<html>" + b"x" * 100_000

    def body():
        yield gzip.compress(payload) if encoding == "gzip" else payload[:1001]
        pytest.fail("Read more bytes after the article budget was exceeded")

    use_pool(
        monkeypatch,
        [httpcore.Response(200, headers={"content-encoding": encoding}, content=body())],
    )
    with pytest.raises(FeedError) as error:
        fetch_article_page("https://example.com/article")
    assert error.value.reason == "response_too_large"
    assert error.value.limit_bytes == 1000 and error.value.status == 200
    assert not error.value.retryable


def test_article_wire_limit_applies_even_when_decoded_body_fits(monkeypatch):
    from devfeed_core.config import get_settings

    payload = b"<html>Small article</html>"
    wire = gzip.compress(payload)
    assert len(payload) < len(wire)
    monkeypatch.setattr(get_settings(), "article_page_max_bytes", len(payload))
    use_pool(
        monkeypatch,
        [httpcore.Response(200, headers={"content-encoding": "gzip"}, content=wire)],
    )
    with pytest.raises(FeedError) as error:
        fetch_article_page("https://example.com/article")
    assert error.value.reason == "response_too_large"


def test_article_fetch_rejects_non_html_before_reading(monkeypatch):
    def body():
        pytest.fail("Read a non-HTML article body")
        yield b""

    use_pool(
        monkeypatch,
        [httpcore.Response(200, headers={"content-type": "application/pdf"}, content=body())],
    )
    with pytest.raises(FeedError) as error:
        fetch_article_page("https://example.com/article")
    assert error.value.reason == "unsupported_content_type"
