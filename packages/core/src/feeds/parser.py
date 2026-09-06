import calendar
import logging
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

import feedparser

from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.source_profiles import SourceProfile, feed_profile
from devfeed_core.source_types import SourceType
from devfeed_core.urls import canonicalize_url, fingerprint, validate_public_url

logger = logging.getLogger(__name__)


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.suppressed += 1
        elif tag in {"p", "br", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.suppressed = max(0, self.suppressed - 1)
        elif tag in {"p", "div", "li"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(data)


def plain_text(value: str, limit: int) -> str:
    parser = TextExtractor()
    parser.feed(value[:100_000])
    return re.sub(r"\s+", " ", "".join(parser.parts)).replace("\x00", "").strip()[:limit]


@dataclass(frozen=True)
class ParsedArticle:
    entry_key: str
    canonical_url: str
    title: str
    summary: str
    author: str | None
    published_at: datetime | None
    tags: list[str]
    source_type: SourceType
    feed_at: datetime
    source_metadata: dict
    image_url: str | None = None
    language: str | None = None
    original_url: str | None = None


@dataclass(frozen=True)
class ParsedFeed:
    entries: list[ParsedArticle]
    seen: int
    skipped: int
    title: str | None = None
    profile: SourceProfile = SourceProfile()


def parse_feed(body: bytes, base_url: str, now: datetime, *, source_type: SourceType) -> ParsedFeed:
    source_type = SourceType(source_type)
    parsed = feedparser.parse(body)
    if not parsed.get("version") or (parsed.bozo and not parsed.entries):
        raise FeedError("Response is not a readable RSS or Atom feed", reason="unreadable_feed")
    entries = []
    skipped = 0
    raw_entries = parsed.entries[: get_settings().feed_max_entries]
    language = parsed.feed.get("language", "").lower()
    if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", language) or len(language) > 35:
        language = None
    for entry in raw_entries:
        try:
            title = plain_text(str(entry.get("title", "")), 500)
            link = entry.get("link", "")
            if not title or not link:
                raise ValueError("Missing title or article URL")
            url = canonicalize_url(urljoin(base_url, link))
            # Avoid feedparser's compatibility alias from updated to published.
            dates = dict(entry)
            published = entry_date(dates.get("published_parsed"), now)
            updated = entry_date(dates.get("updated_parsed"), now)
            summary = plain_text(str(entry.get("summary", "")), 2000)
            author = plain_text(str(entry.get("author", "")), 200) or None
            tags = [plain_text(str(tag.get("term", "")), 100) for tag in entry.get("tags", [])[:30]]
            image_url = entry_image(entry, base_url)
            publisher = source_type == SourceType.PUBLISHER
            # A curated submission's description/byline/date describe the submission,
            # not its linked resource. Preserve them without promoting them to facts.
            source_metadata = {
                "title": title,
                "description": summary,
                "author" if publisher else "submitter": author,
                "published_at" if publisher else "submitted_at": (
                    published.isoformat() if published else None
                ),
                "updated_at": updated.isoformat() if updated else None,
                "discussion_url": optional_url(entry.get("comments"), base_url),
                "tags": tags,
                "image_url": image_url,
                "language": language,
            }
            entries.append(
                ParsedArticle(
                    entry_key=fingerprint(str(entry.get("id") or url)),
                    canonical_url=url,
                    title=title,
                    summary=summary if publisher else "",
                    author=author if publisher else None,
                    published_at=published if publisher else None,
                    tags=tags,
                    source_type=source_type,
                    feed_at=published or updated or now,
                    source_metadata=source_metadata,
                    image_url=image_url if publisher else None,
                    # A multilingual publication can declare a site-wide language.
                    # Preserve that evidence above; article language is inferred by
                    # workers from publisher text, never during source validation.
                    language=None,
                    original_url=validate_public_url(urljoin(base_url, link)),
                )
            )
        except (ValueError, TypeError, AttributeError):
            skipped += 1
    logger.debug(
        "feed_parsed",
        extra={
            "entries_seen": len(parsed.entries),
            "entries_skipped": skipped + len(parsed.entries) - len(raw_entries),
        },
    )
    return ParsedFeed(
        entries,
        len(parsed.entries),
        skipped + len(parsed.entries) - len(raw_entries),
        title=plain_text(str(parsed.feed.get("title") or ""), 200).strip() or None,
        profile=feed_profile(parsed.feed, base_url),
    )


def entry_date(value, now: datetime) -> datetime | None:
    if value:
        with suppress(ValueError, OverflowError, OSError, TypeError):
            date = datetime.fromtimestamp(calendar.timegm(value), UTC)
            if date.year >= 1970 and date <= now:
                return date
    return None


def optional_url(value, base_url: str) -> str | None:
    if value:
        with suppress(ValueError, TypeError):
            return validate_public_url(urljoin(base_url, value))
    return None


def entry_image(entry, base_url: str) -> str | None:
    candidates = [item.get("url") for item in entry.get("media_thumbnail", [])]
    candidates.extend(
        item.get("url")
        for item in entry.get("media_content", [])
        if item.get("medium") == "image" or item.get("type", "").startswith("image/")
    )
    candidates.extend(
        item.get("href")
        for item in entry.get("enclosures", [])
        if item.get("type", "").startswith("image/")
    )
    for candidate in candidates:
        if candidate:
            try:
                return validate_public_url(urljoin(base_url, candidate))
            except ValueError:
                continue
    return None
