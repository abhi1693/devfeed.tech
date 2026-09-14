"""Bounded, robots-aware discovery using the ingestion network guard."""

import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from protego import Protego

from devfeed_core.feeds.fetcher import FeedError, _fetch
from devfeed_core.feeds.parser import parse_feed
from devfeed_core.models import utcnow
from devfeed_core.source_types import SourceType
from devfeed_core.urls import validate_public_url


class CrawlSession:
    def __init__(self):
        self.deadline = time.monotonic() + 120
        self.requests = 0
        self.last_request: dict[str, float] = {}
        self.robots: dict[str, Protego | None] = {}
        self.blocked: dict[str, FeedError] = {}
        self.active_origin: str | None = None

    def before_request(self, url: str, *, robots: bool = False):
        url = validate_public_url(url)
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        if origin in self.blocked:
            raise self.blocked[origin]
        if not robots:
            if origin not in self.robots:
                try:
                    response = self.get(origin + "/robots.txt", robots=True)
                except FeedError as exc:
                    if exc.status in {404, 410}:
                        self.robots[origin] = None
                    else:
                        raise FeedError(
                            "Robots unavailable",
                            reason="robots_unavailable",
                            retryable=exc.retryable,
                            retry_after=exc.retry_after,
                        ) from exc
                else:
                    parser = Protego.parse(response.body.decode("utf-8", errors="replace"))
                    self.robots[origin] = parser
            rules = self.robots[origin]
            if rules is not None and not rules.can_fetch(url, "DevFeed"):
                raise FeedError("Robots disallows discovery", reason="robots_denied")
        if self.requests >= 20 or time.monotonic() >= self.deadline:
            raise FeedError("Discovery budget exhausted", reason="discovery_budget", retryable=True)
        host = parts.hostname or ""
        rules = self.robots.get(origin)
        gap = max(1.0, float(rules.crawl_delay("DevFeed") or 0)) if rules else 1.0
        rate = rules.request_rate("DevFeed") if rules else None
        if rate and rate.requests:
            gap = max(gap, rate.seconds / rate.requests)
        delay = max(0.0, self.last_request.get(host, 0) + gap - time.monotonic())
        if time.monotonic() + delay >= self.deadline:
            raise FeedError("Discovery budget exhausted", reason="discovery_budget", retryable=True)
        time.sleep(delay)
        self.last_request[host] = time.monotonic()
        self.active_origin = origin
        self.requests += 1

    def get(self, url: str, *, robots: bool = False):
        try:
            return _fetch(
                url,
                None,
                None,
                accept="application/rss+xml, application/atom+xml, text/html, */*",
                max_bytes=1_000_000 if robots else 2_000_000,
                timeout=10,
                before_request=lambda value: self.before_request(value, robots=robots),
                deadline=self.deadline,
            )
        except FeedError as exc:
            if exc.status == 429:
                parts = urlsplit(url)
                self.blocked[urlunsplit((parts.scheme, parts.netloc, "", "", ""))] = exc
                if self.active_origin:
                    self.blocked[self.active_origin] = exc
            raise


class Links(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.feeds: list[str] = []
        self.pages: list[str] = []
        self.base_seen = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        href = values.get("href")
        if not href:
            return
        try:
            url = validate_public_url(urljoin(self.base, href))
        except ValueError:
            return
        if tag == "base" and not self.base_seen:
            self.base, self.base_seen = url, True
            return
        if (
            tag == "link"
            and "alternate" in (values.get("rel") or "").lower().split()
            and (values.get("type") or "").lower()
            in {"application/rss+xml", "application/atom+xml"}
        ):
            self.feeds.append(url)
        if tag == "a":
            path = urlsplit(url).path.lower().rstrip("/")
            if path.endswith(("/rss", "/feed", "/atom", ".rss", ".xml")):
                self.feeds.append(url)
            elif urlsplit(url).netloc == urlsplit(self.base).netloc and path.endswith(
                ("/blog", "/news")
            ):
                self.pages.append(url)


def feed_evidence(body: bytes, url: str, homepage: str) -> dict:
    parsed = parse_feed(body, url, utcnow(), source_type=SourceType.PUBLISHER)
    if not parsed.entries:
        raise FeedError("Feed contains no usable entries", reason="unusable_entries")
    entries = parsed.entries[:10]
    home = urlsplit(homepage)
    shared = home.hostname in {"medium.com", "www.medium.com", "blogger.com", "www.blogger.com"}
    prefix = home.path.rstrip("/") + "/"

    def owned(value):
        part = urlsplit(value)
        return part.hostname == home.hostname and (
            not shared or (prefix != "/" and part.path.startswith(prefix))
        )

    ratio = sum(owned(entry.canonical_url) for entry in entries) / len(entries) if entries else None
    dates = [
        entry.published_at
        for entry in entries
        if entry.published_at and entry.published_at <= utcnow()
    ]
    newest = max(dates) if dates else None
    recent = newest is not None and (utcnow() - newest).days <= 180
    publisher_type = "unknown"
    if len(entries) >= 3 and ratio is not None:
        publisher_type = (
            ("hosted_publisher" if shared else "direct")
            if ratio >= 0.8
            else ("probable_aggregator" if ratio < 0.5 else "unknown")
        )
    return {
        "title": parsed.title,
        "format": parsed.format,
        "usable_entries": len(parsed.entries),
        "seen_entries": parsed.seen,
        "publisher_ratio": ratio,
        "publisher_type": publisher_type,
        "recent": recent,
        "newest_article_at": newest.isoformat() if newest else None,
        "sample": [
            {
                "title": entry.title,
                "summary": entry.summary[:2000],
                "url": entry.canonical_url,
                "publisher_owned": owned(entry.canonical_url),
                "published_at": entry.published_at.isoformat() if entry.published_at else None,
            }
            for entry in entries
        ],
    }


def discover(homepage: str, hints: list[str]) -> dict:
    client = CrawlSession()
    feeds: dict[str, dict] = {}
    attempts: list[dict] = []
    visited: set[str] = set()
    pending = [(url, "hint") for url in hints[:5]] + [(homepage, "homepage")]
    scope = homepage.rstrip("/") + "/"
    fallback = [
        urljoin(scope, path)
        for path in (
            "feed/",
            "rss",
            "rss.xml",
            "atom.xml",
            "feed.xml",
            "index.xml",
            "blog/feed/",
            "blog/rss.xml",
        )
    ]
    while pending or fallback:
        if len(visited) >= 14 or client.requests >= 20 or time.monotonic() >= client.deadline:
            break
        url, method = pending.pop(0) if pending else (fallback.pop(0), "conventional")
        if url in visited:
            continue
        visited.add(url)
        try:
            response = client.get(url)
            try:
                evidence = feed_evidence(response.body, response.final_url, homepage)
            except (FeedError, ValueError):
                evidence = None
            if evidence is not None:
                feeds[response.final_url] = {
                    "url": response.final_url,
                    "method": method,
                    **evidence,
                }
                attempts.append({"url": url, "outcome": "valid_feed"})
                # A supplied RSS hint must not prevent finding an Atom alternative.
                # Advertised links are still inspected, under the same crawl budget.
                if evidence["format"] == "atom":
                    fallback.clear()
                else:
                    fallback = [
                        value
                        for value in fallback
                        if urlsplit(value).path.endswith(("atom.xml", "feed.xml", "index.xml"))
                    ]
            else:
                attempts.append({"url": url, "outcome": "not_feed"})
                if method in {"homepage", "blog"}:
                    links = Links(response.final_url)
                    links.feed(response.body.decode("utf-8", errors="replace"))
                    pending.extend((link, "autodiscovery") for link in links.feeds[:5])
                    if method == "homepage":
                        pending.extend((link, "blog") for link in links.pages[:2])
        except FeedError as exc:
            attempts.append(
                {
                    "url": url,
                    "outcome": exc.reason,
                    "retryable": exc.retryable,
                    "retry_after": exc.retry_after,
                }
            )
        except ValueError:
            attempts.append({"url": url, "outcome": "unsafe_url"})
    return {
        "feeds": sorted(feeds.values(), key=lambda feed: feed.get("format") != "atom"),
        "attempts": attempts,
        "requests": client.requests,
        "retry_after": max((item.get("retry_after", 0) for item in attempts), default=0),
        "retryable": not feeds and any(item.get("retryable") for item in attempts),
    }
