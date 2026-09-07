import uuid
from datetime import UTC, datetime

import pytest
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.feeds.parser import parse_feed, plain_text
from devfeed_core.models import Tag, Topic
from devfeed_core.taxonomy import classify, classify_tags, detect_content_type
from devfeed_core.urls import canonicalize_url, validate_public_url

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def test_rss_metadata_sanitization_and_invalid_entry_isolation(rss_bytes):
    result = parse_feed(rss_bytes, "https://example.com/feed.xml", NOW, source_type="publisher")
    assert (result.seen, result.skipped, len(result.entries)) == (3, 1, 2)
    assert result.title == "Engineering Example"
    article = result.entries[0]
    assert article.canonical_url == "https://example.com/python?version=2"
    assert article.summary == "A practical PostgreSQL tutorial."
    assert article.image_url == "https://example.com/cover.jpg"
    assert article.language is None  # Worker inference, not the feed's blanket declaration.
    assert article.source_metadata["language"] == "en"
    assert "utm_source" in article.original_url
    assert article.published_at == datetime(2026, 9, 4, 8, tzinfo=UTC)


def test_atom_relative_link_and_future_date():
    body = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title>
    <entry><id>urn:test:1</id><title>Rust tutorial</title><link href="/rust"/>
    <updated>2099-01-01T00:00:00Z</updated><summary>Learn Rust</summary></entry></feed>"""
    result = parse_feed(body, "https://example.com/atom.xml", NOW, source_type="publisher")
    assert result.entries[0].canonical_url == "https://example.com/rust"
    assert result.entries[0].published_at is None
    assert result.entries[0].feed_at == NOW
    assert result.title == "Test"


@pytest.mark.parametrize(
    "title,expected",
    [
        (
            "<title><![CDATA[ <b>Engineering</b> &amp; Tools <script>hidden</script> ]]></title>",
            "Engineering & Tools",
        ),
        ("<title>  Développeurs\n   日本語  </title>", "Développeurs 日本語"),
        ("<title>  </title>", None),
        ("", None),
        (f"<title>{'x' * 250}</title>", "x" * 200),
    ],
)
def test_feed_title_is_clean_bounded_and_optional(title, expected):
    body = f'<rss version="2.0"><channel>{title}</channel></rss>'.encode()
    parsed = parse_feed(body, "https://example.com/rss", NOW, source_type="publisher")
    assert parsed.title == expected


def test_atom_xhtml_feed_title():
    body = b"""<feed xmlns="http://www.w3.org/2005/Atom">
    <title type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">
    <b>Engineering</b> &amp; Tools</div></title></feed>"""
    parsed = parse_feed(body, "https://example.com/atom", NOW, source_type="aggregator")
    assert parsed.title == "Engineering & Tools"


def test_empty_feed_is_valid_but_html_and_broken_xml_are_not():
    result = parse_feed(
        b'<rss version="2.0"><channel><title>Empty</title></channel></rss>',
        "https://example.com/rss",
        NOW,
        source_type="publisher",
    )
    assert result.entries == []
    for invalid in [b"<html><title>Not RSS</title></html>", b"not xml", b"<rss version='2.0'>"]:
        with pytest.raises(FeedError):
            parse_feed(invalid, "https://example.com/rss", NOW, source_type="publisher")


def test_classification_uses_boundaries_and_aliases():
    topic = Topic(
        kind="discipline",
        status="active",
        id=uuid.uuid4(),
        name="Go",
        slug="go",
        keywords=["golang"],
    )
    assert classify("Going outside", "", [], [topic]) == []
    assert classify("Golang performance", "", [], [topic]) == [topic.id]
    tags = [
        Tag(id=uuid.uuid4(), name=name, slug=slug, aliases=aliases)
        for name, slug, aliases in [
            ("Kubernetes", "kubernetes", ["k8s"]),
            ("React", "react", ["reactjs"]),
            ("Vue", "vue", ["vue.js"]),
        ]
    ]
    assert classify_tags("Vue.js and k8s", "", ["reactjs"], tags) == [tag.id for tag in tags]
    assert classify_tags("Going for a walk", "", [], tags) == []
    assert classify_tags("Python and Kubernetes", "", [], []) == []
    assert detect_content_type("How to use Python", []) == "tutorial"


def test_plain_text_drops_active_markup_and_limits_output():
    assert plain_text("<style>hide</style><p>Hello &amp; goodbye</p>", 100) == "Hello & goodbye"
    assert plain_text("x" * 500, 20) == "x" * 20


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/rss",
        "http://10.0.0.1/rss",
        "http://169.254.169.254/latest",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://localhost/",
        "http://nas.local/",
        "file:///etc/passwd",
        "https://user:pass@example.com/rss",
        "http://example.com:8000/",
        "https://example.com/\r\nHost:evil.com",
        "http://2130706433/",
        "http://127.1/",
    ],
)
def test_private_or_unsafe_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_url_normalization_preserves_semantic_query_and_path():
    assert canonicalize_url("https://EXAMPLE.com:443/a?x=1&utm_source=feed#part") == (
        "https://example.com/a?x=1"
    )
    signed = "https://example.com/a?signature=one%20two&b=2&a=1"
    assert canonicalize_url(signed) == signed
    assert canonicalize_url("https://example.com/a/") != canonicalize_url("https://example.com/a")
