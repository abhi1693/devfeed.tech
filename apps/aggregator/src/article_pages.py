"""Extract a bounded preview from already-downloaded HTML, without any network helpers."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.feeds.parser import plain_text
from devfeed_core.images import ImageParser, decode_html, extract_image
from trafilatura import bare_extraction, extract_metadata

from devfeed_aggregator.languages import detect_language, letter_count, prose

SUMMARY_LIMIT = 500
BLOCKED_TITLES = {
    "just a moment",
    "access denied",
    "verify you are human",
    "checking your browser",
    "robot or human",
    "sign in",
    "log in",
    "403 forbidden",
    "404 not found",
    "making sure you're not a bot",
    "please wait",
}


class MetadataParser(ImageParser):
    def __init__(self, base: str):
        super().__init__(base)
        self.declared_language: str | None = None

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if self.ignored:
            return
        values = dict(attrs)
        if tag == "html":
            self.declared_language = (values.get("lang") or "")[:35] or None
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower().strip()
            if key in {
                "article:published_time",
                "datepublished",
                "pubdate",
                "author",
                "citation_author",
            }:
                candidates = self.meta.setdefault(key, [])
                if len(candidates) < 20 and values.get("content"):
                    candidates.append(values["content"][:1000])


def structured_nodes(documents: list[str]):
    for document in documents:
        try:
            pending = [(json.loads(document), 0)]
        except (ValueError, RecursionError):
            continue
        visited = 0
        while pending and visited < 1000:
            node, depth = pending.pop()
            visited += 1
            if depth > 12:
                continue
            if isinstance(node, list):
                pending.extend((item, depth + 1) for item in reversed(node[:100]))
            elif isinstance(node, dict):
                yield node
                for key in ("@graph", "mainEntity"):
                    if key in node:
                        pending.append((node[key], depth + 1))


def is_article(node: dict) -> bool:
    types = node.get("@type", [])
    types = [types] if isinstance(types, str) else types
    return isinstance(types, list) and any(
        isinstance(kind, str)
        and kind.rsplit("/", 1)[-1]
        in {"Article", "NewsArticle", "BlogPosting", "TechArticle", "Report"}
        for kind in types
    )


def article_nodes(documents: list[str]):
    return (node for node in structured_nodes(documents) if is_article(node))


def structured_author(documents: list[str]) -> str | None:
    nodes = list(structured_nodes(documents))
    references = {node["@id"]: node for node in nodes if isinstance(node.get("@id"), str)}
    for node in nodes:
        if not is_article(node):
            continue
        authors = node.get("author", [])
        authors = authors if isinstance(authors, list) else [authors]
        names = []
        for author in authors[:10]:
            if isinstance(author, dict):
                reference = author.get("@id")
                if isinstance(reference, str):
                    author = references.get(reference, author)
                author = author.get("name")
            if isinstance(author, str) and "://" not in author:
                name = plain_text(author, 200)
                if name and name not in names:
                    names.append(name)
        if names:
            return "; ".join(names)[:200]
    return None


def publication_date(parser: MetadataParser, now: datetime) -> datetime | None:
    candidates = [
        item
        for key in ("article:published_time", "datepublished", "pubdate")
        for item in parser.meta.get(key, [])
    ]
    candidates += [node.get("datePublished") for node in article_nodes(parser.documents)]
    for value in candidates:
        if not isinstance(value, str):
            continue
        try:
            date = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            # Do not invent a timezone or substitute dateModified/URL/copyright dates.
            if date.tzinfo is not None and date.year >= 1970 and date <= now:
                return date.astimezone(UTC)
        except (ValueError, OverflowError):
            continue
    return None


def excerpt(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= SUMMARY_LIMIT:
        return text
    cut = text[: SUMMARY_LIMIT - 1]
    # Prefer complete sentences; otherwise use a word boundary (CJK needs no spaces).
    boundaries = list(re.finditer(r"[.!?。！？](?:\s|$)", cut))
    if boundaries and boundaries[-1].end() >= SUMMARY_LIMIT // 2:
        return cut[: boundaries[-1].start() + 1]
    boundary = cut.rfind(" ")
    if boundary >= SUMMARY_LIMIT // 2:
        cut = cut[:boundary]
    return cut.rstrip() + "…"


@dataclass(frozen=True)
class PageArticle:
    title: str | None
    summary: str
    author: str | None
    published_at: datetime | None
    image_url: str | None
    language: str | None
    text_source: str | None
    evidence: dict
    text: str = ""

    @property
    def has_text(self) -> bool:
        return self.text_source is not None


def extract_article(result: FetchResult, now: datetime) -> PageArticle:
    html = decode_html(result)
    parser = MetadataParser(result.final_url)
    parser.feed(html)
    parser.close()
    # No comments, code, nav/chrome or hidden teaser/paywall elements in the sample.
    prune = [
        "//pre",
        "//code",
        "//nav",
        "//footer",
        "//aside",
        "//form",
        "//*[@hidden]",
        "//*[@aria-hidden='true']",
        "//*[@data-nosnippet]",
    ]
    document = bare_extraction(
        html,
        url=result.final_url,
        with_metadata=True,
        include_comments=False,
        include_tables=False,
        include_links=False,
        favor_precision=True,
        prune_xpath=prune,
        date_extraction_params={"extensive_search": False},
    )
    # The dict alternative is only returned by the deprecated as_dict opt-in.
    assert not isinstance(document, dict)
    metadata = document or extract_metadata(
        html, default_url=result.final_url, date_config={"extensive_search": False}
    )
    title = plain_text(metadata.title or "", 500) or None
    if title and title.lower().strip(" .!?:…") in BLOCKED_TITLES:
        raise FeedError("Page is a login, error or browser challenge", reason="page_unavailable")
    # A publisher may expose a public description without making its full text available.
    paywalled = any(
        node.get("isAccessibleForFree") in (False, "False", "false")
        for node in article_nodes(parser.documents)
    )
    body = prose(document.text or "") if document is not None and not paywalled else ""
    description = prose(metadata.description or "")
    text, basis = (
        (body, "body")
        if letter_count(body) >= 100
        else ((description, "description") if letter_count(description) >= 40 else ("", None))
    )
    # Do not use an aggregator headline or the page's declared language as a fallback.
    detected = detect_language("", text, "page")
    image = extract_image(result)
    values = parser.meta.get("author", []) or parser.meta.get("citation_author", [])
    author = (
        structured_author(parser.documents)
        or plain_text("; ".join(values), 200)
        or plain_text(metadata.author or "", 200)
        or None
    )
    published = publication_date(parser, now)
    summary = excerpt(description if letter_count(description) >= 40 else text) if text else ""
    # Keep a bounded extraction privately for analysis; never return it in the feed.
    return PageArticle(
        title,
        summary,
        author,
        published,
        image.url if image else None,
        detected.language if text else None,
        basis,
        {
            "method": "trafilatura",
            "final_url": result.final_url,
            "title": title,
            "summary": summary,
            "author": author,
            "published_at": published.isoformat() if published else None,
            "image_url": image.url if image else None,
            "image_method": image.method if image else None,
            "language": detected.language if text else None,
            "language_confidence": detected.confidence if text else None,
            "language_reason": detected.reason if text else "insufficient_text",
            "declared_language": parser.declared_language,
            "text_source": basis,
            "sample_characters": len(text),
            "paywalled": paywalled,
        },
        text[:60_000],
    )
