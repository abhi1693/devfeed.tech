import gzip
import json

import httpcore
import pytest
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult, fetch_page
from devfeed_core.images import PageImage, extract_image
from test_fetcher import use_pool

PAGE = "https://publisher.example/posts/article"


def extract(html, **kwargs):
    return extract_image(FetchResult(200, html.encode(), PAGE, **kwargs))


def test_open_graph_wins_over_twitter_and_preserves_signed_query():
    found = extract("""<meta name="twitter:image" content="/twitter.png">
        <meta property="og:image" content="//cdn.example/cover?sig=a%2Fb&amp;utm_key=x">
        <meta property="og:image" content="/second.png">""")
    assert found == PageImage("https://cdn.example/cover?sig=a%2Fb&utm_key=x", "og:image")


def test_og_alias_preserves_document_order_and_secure_url_belongs_to_its_image():
    assert extract("""<meta property="og:image:url" content="/first.png">
        <meta property="og:image" content="http://cdn.example/second.png">
        <meta property="og:image:secure_url" content="https://cdn.example/second.png">""").url.endswith(
        "/first.png"
    )
    assert (
        extract("""<meta property="og:image" content="http://cdn.example/first.png">
        <meta property="og:image:secure_url" content="https://cdn.example/first.png">""")
        == PageImage("https://cdn.example/first.png", "og:image:secure_url")
    )


@pytest.mark.parametrize(
    "key", ["twitter:image", "twitter:image:src", "og:image", "og:image:url", "og:image:secure_url"]
)
def test_meta_case_entities_relative_urls_and_self_closing_tags(key):
    assert (
        extract(f'<META NAME="{key.upper()}" CONTENT="../cover.jpg?a=1&amp;b=2"/>').url
        == "https://publisher.example/cover.jpg?a=1&b=2"
    )


def test_base_url_and_final_redirect_url_resolve_relative_images():
    assert (
        extract("""<meta property="og:image" content="cover.jpg">
        <base href="https://cdn.example/assets/"><base href="https://ignored.example/">""").url
        == "https://cdn.example/assets/cover.jpg"
    )
    found = extract_image(
        FetchResult(
            200,
            b'<meta property="og:image" content="cover.jpg">',
            "https://redirected.example/final/post",
        )
    )
    assert found.url == "https://redirected.example/final/cover.jpg"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/cover",
        "http://169.254.169.254/cover",
        "http://[::1]/cover",
        "http://localhost/cover",
        "http://private.internal/cover",
        "file:///cover",
        "javascript:alert(1)",
        "data:image/png;base64,anything",
        "https://secret:password@cdn.example/cover",
        "https://cdn.example:444/cover",
        "https://cdn.example/a b",
        "https://cdn.example/" + "a" * 2048,
    ],
)
def test_unsafe_image_candidates_are_skipped_in_favor_of_valid_fallback(url):
    assert extract(
        f'<meta property="og:image" content="{url}"><meta name="twitter:image" content="/ok.png">'
    ) == PageImage("https://publisher.example/ok.png", "twitter:image")


def test_private_base_is_ignored_without_rejecting_public_absolute_metadata():
    assert (
        extract(
            '<base href="http://127.0.0.1/"><meta property="og:image" content="/public.png">'
        ).url
        == "https://publisher.example/public.png"
    )


@pytest.mark.parametrize(
    "value",
    [
        "/cover.png",
        ["/cover.png"],
        {"url": "/cover.png"},
        {"@type": "ImageObject", "contentUrl": "/cover.png"},
    ],
)
def test_article_json_ld_image_shapes(value):
    document = {"@context": "https://schema.org", "@type": "BlogPosting", "image": value}
    assert extract(
        f'<script type="application/ld+json">{json.dumps(document)}</script>'
    ) == PageImage("https://publisher.example/cover.png", "json_ld")


def test_json_ld_graph_reference_prefers_article_over_organization_and_page():
    document = {
        "@graph": [
            {"@type": "Organization", "image": "/logo.png"},
            {"@type": "WebPage", "image": "/page.png"},
            {"@type": ["https://schema.org/NewsArticle"], "image": {"@id": "#cover"}},
            {"@id": "#cover", "@type": "ImageObject", "url": "//cdn.example/cover.png"},
        ]
    }
    assert (
        extract(f'<script type="application/ld+json">{json.dumps(document)}</script>').url
        == "https://cdn.example/cover.png"
    )


def test_missing_malformed_and_irrelevant_metadata_is_a_normal_absence():
    assert (
        extract("""<img src="/logo.png"><script>var image = '/track.gif'</script>
        <script type="application/ld+json">not json</script>
        <script type="application/ld+json">{"@type":"Person","image":"/avatar.png"}</script>
        <template><meta property="og:image" content="/template.png"></template>""")
        is None
    )


def test_json_ld_invalid_shapes_and_excessive_depth_are_safe():
    for document in ["null", "42", '"hello"', '{"@type":42,"image":[]}', "[" * 2000 + "]" * 2000]:
        assert extract(f'<script type="application/ld+json">{document}</script>') is None


def test_html_charset_and_unknown_encoding_fallback():
    body = '<meta property="og:image" content="/café.png">'.encode("windows-1252")
    assert extract_image(
        FetchResult(200, body, PAGE, content_type="text/html; charset=windows-1252")
    ).url.endswith("/café.png")
    assert extract(
        '<meta property="og:image" content="/cover.png">',
        content_type="text/html; charset=nonexistent",
    ).url.endswith("/cover.png")


def test_page_fetch_sends_html_accept_and_never_feed_validators(monkeypatch):
    pool = use_pool(
        monkeypatch,
        [
            httpcore.Response(
                200, headers={"content-type": "text/html; charset=utf-8"}, content=b"<html></html>"
            )
        ],
    )
    result = fetch_page(PAGE)
    assert result.content_type == "text/html; charset=utf-8"
    assert "text/html" in pool.requests[0][1]["Accept"]
    assert "If-None-Match" not in pool.requests[0][1]


@pytest.mark.parametrize("status,retryable", [(304, False), (404, False), (429, True), (503, True)])
def test_page_http_failures_have_retry_semantics(monkeypatch, status, retryable):
    use_pool(monkeypatch, [httpcore.Response(status, headers={"retry-after": "120"})])
    with pytest.raises(FeedError) as error:
        fetch_page(PAGE)
    assert (error.value.status, error.value.retryable, error.value.retry_after) == (
        status,
        retryable,
        120,
    )


@pytest.mark.parametrize("content_type", ["application/pdf", "application/json", "image/png"])
def test_nonhtml_content_is_rejected_without_reading_body(monkeypatch, content_type):
    def body():
        pytest.fail("Read non-HTML body")
        yield b""

    use_pool(
        monkeypatch,
        [httpcore.Response(200, headers={"content-type": content_type}, content=body())],
    )
    with pytest.raises(FeedError) as error:
        fetch_page(PAGE)
    assert error.value.reason == "unsupported_content_type" and not error.value.retryable


def test_page_redirect_and_gzip_limits_share_safe_transport(monkeypatch):
    use_pool(monkeypatch, [httpcore.Response(302, headers={"location": "http://169.254.169.254/"})])
    with pytest.raises(FeedError):
        fetch_page(PAGE)
    monkeypatch.setattr(get_settings(), "page_max_bytes", 100)
    use_pool(
        monkeypatch,
        [
            httpcore.Response(
                200, headers={"content-encoding": "gzip"}, content=gzip.compress(b"x" * 10000)
            )
        ],
    )
    with pytest.raises(FeedError) as error:
        fetch_page(PAGE)
    assert error.value.reason == "response_too_large"
