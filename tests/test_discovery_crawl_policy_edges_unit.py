"""Real crawl admission enforces robots policy, document navigation and finite budgets."""

from types import SimpleNamespace

import pytest
from devfeed_core import discovery_crawler as crawler
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from test_source_discovery import feed as feed
from test_source_readiness_unit import NOW

HOME = "https://publisher.example/"


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=100.0)
    monkeypatch.setattr(
        crawler,
        "time",
        SimpleNamespace(
            monotonic=lambda: value.now,
            sleep=lambda delay: setattr(value, "now", value.now + delay),
        ),
    )
    monkeypatch.setattr(crawler, "utcnow", lambda: NOW)
    return value


@pytest.mark.parametrize("missing_status", [404, 410])
def test_absent_robots_file_allows_bounded_content_requests_without_repeating_lookup(
    monkeypatch, clock, missing_status
):
    calls = []

    def fetch(url, *_args, before_request, **_options):
        before_request(url)
        calls.append(url)
        if url.endswith("/robots.txt"):
            raise FeedError("No robots document", reason="http_error", status=missing_status)
        return FetchResult(200, b"<p>Public publication</p>", url, content_type="text/html")

    monkeypatch.setattr(crawler, "_fetch", fetch)
    client = crawler.CrawlSession()

    assert client.get(HOME).status == 200
    assert client.get(HOME + "blog").status == 200
    assert calls == [HOME + "robots.txt", HOME, HOME + "blog"]
    assert client.requests == 3


def test_elapsed_crawl_budget_preserves_already_verified_feeds_and_stops_fallbacks(
    monkeypatch, clock, feed
):
    calls = []

    def fetch(url, *_args, before_request, **_options):
        before_request(url)
        calls.append(url)
        if url.endswith("/robots.txt"):
            raise FeedError("No robots document", reason="http_error", status=404)
        return FetchResult(200, feed, url, content_type="application/rss+xml")

    monkeypatch.setattr(crawler, "_fetch", fetch)
    inspect = crawler.feed_evidence

    def verify_then_consume_budget(*args):
        evidence = inspect(*args)
        clock.now += 121  # Processing or a process pause consumes the remaining crawl budget.
        return evidence

    monkeypatch.setattr(crawler, "feed_evidence", verify_then_consume_budget)
    result = crawler.discover(HOME, [])

    assert [row["url"] for row in result["feeds"]] == [HOME]
    assert result["attempts"] == [{"url": HOME, "outcome": "valid_feed"}]
    assert calls == [HOME + "robots.txt", HOME]


def test_blog_feed_anchor_finds_atom_without_expanding_nested_navigation_or_guessed_paths(
    monkeypatch, clock
):
    atom = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title>Engineering</title>'
        + "".join(
            f"<entry><id>{HOME}posts/{index}</id><title>Database design {index}</title>"
            f'<link href="{HOME}posts/{index}"/>'
            "<updated>2026-10-21T10:00:00Z</updated></entry>"
            for index in range(3)
        )
        + "</feed>"
    ).encode()
    pages = {
        HOME: b'<html><a href="/blog">Engineering blog</a></html>',
        HOME + "blog": b'<html><a href="/rss">Feed</a><a href="/news">More news</a></html>',
        HOME + "rss": atom,
    }
    calls = []

    def fetch(url, *_args, before_request, **_options):
        before_request(url)
        calls.append(url)
        if url.endswith("/robots.txt"):
            return FetchResult(200, b"User-agent: *\nAllow: /", url, content_type="text/plain")
        assert url in pages, f"Unexpected crawl expansion: {url}"
        return FetchResult(200, pages[url], url)

    monkeypatch.setattr(crawler, "_fetch", fetch)
    result = crawler.discover(HOME, [])

    assert calls == [HOME + "robots.txt", HOME, HOME + "blog", HOME + "rss"]
    assert len(result["feeds"]) == 1 and result["feeds"][0]["format"] == "atom"
    assert result["feeds"][0]["url"] == HOME + "rss"
    assert result["feeds"][0]["publisher_type"] == "direct"
