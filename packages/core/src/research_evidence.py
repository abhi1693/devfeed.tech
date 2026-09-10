"""Verify cited text against bounded public HTML, independently of model claims.

Quote presence establishes provenance, not the truth of a claim or entailment.
Failures remain reviewable and never authorize automatic approval.
"""

import hashlib
import json
import time
import unicodedata
from html.parser import HTMLParser

from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, fetch_evidence_page
from devfeed_core.images import decode_html
from devfeed_core.models import utcnow

VERIFICATION_VERSION = "public-quote-v1"
VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        hidden = (
            bool(self.stack and self.stack[-1][1])
            or tag in {"script", "style", "template", "noscript"}
            or "hidden" in attributes
            or attributes.get("aria-hidden") == "true"
        )
        if tag not in VOID:
            self.stack.append((tag, hidden))
        if not hidden and tag in {"p", "div", "br", "li", "td", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in {"p", "div", "li", "td", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_data(self, value):
        if not self.stack or not self.stack[-1][1]:
            self.parts.append(value)


def citation_key(url: str, quote: str) -> str:
    return hashlib.sha256(json.dumps([url, quote], ensure_ascii=False).encode()).hexdigest()


def verify_citations(citations: list[tuple[str, str]]) -> dict:
    deadline = time.monotonic() + get_settings().evidence_timeout_seconds
    pages: dict[str, dict] = {}
    checks: dict[str, dict] = {}
    for url, quote in citations[:20]:
        key = citation_key(url, quote)
        if key in checks:
            continue
        if url not in pages:
            remaining = deadline - time.monotonic()
            page = {"url": url, "checked_at": utcnow().isoformat()}
            if remaining <= 0:
                page["reason"] = "verification_timeout"
            else:
                try:
                    fetched = fetch_evidence_page(url, remaining)
                    parser = VisibleText()
                    parser.feed(decode_html(fetched))
                    page |= {
                        "final_url": fetched.final_url,
                        "content_hash": hashlib.sha256(fetched.body).hexdigest(),
                        "text": normalized("".join(parser.parts)),
                    }
                except FeedError as exc:
                    page["reason"] = exc.reason
                except (ValueError, UnicodeError, RecursionError):
                    page["reason"] = "unreadable_evidence"
            pages[url] = page
        page = pages[url]
        matched = bool(normalized(quote)) and normalized(quote) in page.get("text", "")
        checks[key] = {
            **{name: value for name, value in page.items() if name != "text"},
            "quote": quote,
            "status": "verified" if matched else "unverified",
            "reason": None if matched else page.get("reason", "quote_not_found"),
        }
    return {"version": VERIFICATION_VERSION, "checks": checks}


def citation_verified(verification: dict, url: str, quote: str) -> bool:
    return (
        verification.get("version") == VERIFICATION_VERSION
        and verification.get("checks", {}).get(citation_key(url, quote), {}).get("status")
        == "verified"
    )
