"""Verify cited text against bounded public HTML, independently of model claims.

Quote presence establishes provenance, not the truth of a claim or entailment.
Failures remain reviewable and never authorize automatic approval.
"""

import hashlib
import json
import re
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
            page: dict = {"url": url, "checked_at": utcnow().isoformat()}
            if remaining <= 0:
                page["reason"] = "verification_timeout"
                page["retryable"] = True
            else:
                try:
                    page |= fetched_page(url, remaining)
                except FeedError as exc:
                    page |= {
                        "reason": exc.reason,
                        "retryable": exc.retryable,
                        "retry_after": exc.retry_after,
                        "http_status": exc.status,
                    }
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


def citation_retryable(check: dict) -> bool:
    if check.get("status") == "verified":
        return False
    if "retryable" in check:
        return check["retryable"] is True
    # Old checks did not retain HTTP status. Give ambiguous HTTP failures one
    # bounded retry; the new fetch records whether another attempt can help.
    return check.get("reason") in {"transport_error", "verification_timeout", "http_error"}


def fetched_page(url: str, timeout: float) -> dict:
    """Reuse parsed public evidence only after a fresh, SSRF-checked HTTP validation.

    Cached quotes are never an approval decision. Errors never serve stale pages.
    """
    from devfeed_core.cache import get_cache

    started = time.monotonic()
    cache, key, previous = None, None, None
    if get_settings().cache_enabled:
        try:
            cache = get_cache()
            key = cache.namespace + ":evidence:v1:" + hashlib.sha256(url.encode()).hexdigest()
            raw = cache.redis.get(key)
            if raw and len(raw) <= get_settings().cache_max_bytes:
                candidate = json.loads(raw)
                if (
                    isinstance(candidate, dict)
                    and all(
                        isinstance(candidate.get(k), str)
                        for k in ("text", "final_url", "content_hash")
                    )
                    and any(
                        isinstance(candidate.get(k), str) and candidate[k]
                        for k in ("etag", "last_modified")
                    )
                ):
                    previous = candidate
        except Exception:
            cache = None
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        raise FeedError("Evidence deadline exceeded", reason="verification_timeout", retryable=True)
    options = (
        {k: previous[k] for k in ("etag", "last_modified") if previous.get(k)} if previous else {}
    )
    fetched = fetch_evidence_page(url, remaining, **options)
    if fetched.status == 304:
        if previous is None or previous["final_url"] != fetched.final_url:
            raise FeedError(
                "Unmatched cache validation", reason="invalid_evidence_revalidation", retryable=True
            )
        page = {k: previous[k] for k in ("final_url", "content_hash", "text")}
    else:
        parser = VisibleText()
        parser.feed(decode_html(fetched))
        page = {
            "final_url": fetched.final_url,
            "content_hash": hashlib.sha256(fetched.body).hexdigest(),
            "text": normalized("".join(parser.parts)),
        }
    cache_control = fetched.cache_control or (previous or {}).get("cache_control", "")
    if cache is not None and key is not None:
        try:
            directives = cache_control.casefold()
            if any(value in directives for value in ("no-store", "private")):
                cache.redis.delete(key)
            elif fetched.status == 200 and (fetched.etag or fetched.last_modified):
                raw = json.dumps(
                    {
                        **page,
                        "etag": fetched.etag,
                        "last_modified": fetched.last_modified,
                        "cache_control": cache_control,
                    }
                ).encode()
                if len(raw) <= get_settings().cache_max_bytes:
                    cache.redis.set(key, raw, ex=300)
        except Exception:
            pass  # Optional cache failure never changes a fresh verification result.
    max_age = re.search(r'(?i)(?:^|,)\s*max-age\s*=\s*"?(\d+)', cache_control)
    page["reuse_seconds"] = min(300, int(max_age[1])) if max_age else 300
    page["validated_at"] = utcnow().isoformat()
    page["reusable"] = not any(
        directive in cache_control.casefold() for directive in ("no-store", "private", "no-cache")
    )
    return page
