"""Bounded publisher-list adapters. Feed URLs are hints, never trusted admission."""

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from devfeed_core.urls import canonicalize_url, validate_public_url

FORMATS = {"opml", "urls", "json", "markdown"}
MAX_IMPORT_BYTES = 1_000_000
MAX_CANDIDATES = 1000


@dataclass(frozen=True)
class PublisherHint:
    homepage: str
    name: str
    feed_hint: str | None = None


def identity_url(value: str) -> str:
    parts = urlsplit(canonicalize_url(value.strip()))
    # Preserve publication paths and query parameters (hosted platforms share hosts).
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", parts.query, ""))


def publisher_hint(url: str, name: str | None = None, feed: str | None = None) -> PublisherHint:
    if (
        not isinstance(url, str)
        or (name is not None and not isinstance(name, str))
        or (feed is not None and not isinstance(feed, str))
    ):
        raise ValueError("Publisher URLs and names must be strings")
    homepage = identity_url(url)
    return PublisherHint(
        homepage,
        (name or urlsplit(homepage).hostname or homepage)[:200],
        validate_public_url(feed) if feed else None,
    )


def parse_import(body: bytes, format: str) -> list[PublisherHint]:
    if len(body) > MAX_IMPORT_BYTES:
        raise ValueError("Import exceeds 1 MB")
    if format not in FORMATS:
        raise ValueError("Unsupported discovery import format")
    text = body.decode("utf-8-sig")
    raw: dict[tuple[str, str | None], PublisherHint] = {}

    def add(hint: PublisherHint):
        raw.setdefault((hint.homepage, hint.feed_hint), hint)
        if len(raw) > MAX_CANDIDATES:
            raise ValueError("Import exceeds 1,000 unique publisher entries")

    if format == "opml":
        # Reject entities/DTDs before the standard XML parser sees the document.
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
            raise ValueError("OPML DTDs and entities are not allowed")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ValueError("Invalid OPML document") from exc
        if root.tag.lower() != "opml":
            raise ValueError("Expected an OPML document")
        for element in root.iter("outline"):
            feed = element.get("xmlUrl") or element.get("xmlurl")
            home = element.get("htmlUrl") or element.get("htmlurl")
            if home or feed:
                # A feed-only OPML entry retains its exact path. The crawler first
                # tests that hint, then inspects its site; never merge hosted tenants.
                add(
                    publisher_hint(
                        home or feed or "", element.get("title") or element.get("text"), feed
                    )
                )
            if len(raw) > MAX_CANDIDATES:
                raise ValueError("Import exceeds 1,000 publisher entries")
    elif format == "json":
        values = json.loads(text)
        if not isinstance(values, list):
            raise ValueError("Expected at most 1,000 publisher objects")
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("homepage_url"), str):
                raise ValueError("Each publisher requires homepage_url")
            add(publisher_hint(item["homepage_url"], item.get("name"), item.get("feed_url")))
    elif format == "markdown":
        # Deliberately bounded Markdown links, not arbitrary HTML/executable repository contents.
        for name, url in re.findall(r"\[([^\]\n]{1,200})\]\((https?://[^\s)]+)\)", text):
            if urlsplit(url).hostname not in {"github.com", "raw.githubusercontent.com"}:
                add(publisher_hint(url, name))
            if len(raw) > MAX_CANDIDATES:
                raise ValueError("Import exceeds 1,000 publisher entries")
    else:
        for line in text.splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                add(publisher_hint(line))
            if len(raw) > MAX_CANDIDATES:
                raise ValueError("Import exceeds 1,000 publisher entries")
    # Retain distinct hints for the same publication; DB provenance coalesces reimports.
    return list(raw.values())
