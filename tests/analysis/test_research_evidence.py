"""Research evidence transport validates citations, caching, and safe failures."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import cache
from devfeed_core import research_evidence as evidence
from devfeed_core.feeds.fetcher import FeedError, FetchResult

from tests.support.source_readiness import configuration


def test_verification_fetches_each_page_once_and_retains_provenance(monkeypatch):
    html = (
        b"<html><p>Example uses <strong>MIT</strong> licensing.</p><p>Written in Rust.</p></html>"
    )
    requests = []

    def fetch(url, timeout):
        requests.append((url, timeout))
        return FetchResult(200, html, "https://example.com/docs")

    monkeypatch.setattr(evidence, "fetch_evidence_page", fetch)
    result = evidence.verify_citations(
        [
            ("https://example.com", "Example uses MIT licensing."),
            ("https://example.com", "Written in Rust."),
        ]
    )
    assert len(requests) == 1 and 0 < requests[0][1] <= 45
    assert evidence.citation_verified(result, "https://example.com", "Written in Rust.")
    check = next(iter(result["checks"].values()))
    assert check["content_hash"] == hashlib.sha256(html).hexdigest()
    assert check["checked_at"] and check["final_url"] == "https://example.com/docs"
    assert "text" not in check


@pytest.mark.parametrize(
    "html",
    [
        "<script>Invented quote</script>",
        "<style>Invented quote</style>",
        "<template>Invented quote</template>",
        "<div hidden><p>Invented quote</p></div>",
        '<div aria-hidden="true">Invented quote</div>',
        "<p>A different claim.</p>",
    ],
)
def test_missing_or_hidden_quotes_cannot_authorize_approval(monkeypatch, html):
    monkeypatch.setattr(
        evidence, "fetch_evidence_page", lambda url, timeout: FetchResult(200, html.encode(), url)
    )
    result = evidence.verify_citations([("https://example.com", "Invented quote")])
    assert not evidence.citation_verified(result, "https://example.com", "Invented quote")
    assert next(iter(result["checks"].values()))["reason"] == "quote_not_found"


@pytest.mark.parametrize(
    "reason", ["private_address", "page_too_large", "feed_timeout", "unexpected_content_type"]
)
def test_transport_failure_stays_unverified_and_does_not_leak_error(monkeypatch, reason):
    def fetch(*args):
        raise FeedError("Untrusted secret-bearing response", reason=reason)

    monkeypatch.setattr(evidence, "fetch_evidence_page", fetch)
    result = evidence.verify_citations([("https://example.com", "Supported quote")])
    check = next(iter(result["checks"].values()))
    assert check["status"] == "unverified" and check["reason"] == reason
    assert "secret-bearing" not in str(result)


def test_overall_deadline_prevents_further_network_requests(monkeypatch):
    ticks = iter([0, 46, 47])
    monkeypatch.setattr(evidence.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        evidence, "fetch_evidence_page", lambda *args: pytest.fail("Fetched after deadline")
    )
    result = evidence.verify_citations(
        [("https://example.com/a", "quote one"), ("https://example.com/b", "quote two")]
    )
    assert {row["reason"] for row in result["checks"].values()} == {"verification_timeout"}


def test_quote_whitespace_normalization_keeps_case_and_word_order(monkeypatch):
    monkeypatch.setattr(
        evidence,
        "fetch_evidence_page",
        lambda url, timeout: FetchResult(200, b"<p>Example&nbsp;uses\n MIT licensing.</p>", url),
    )
    result = evidence.verify_citations([("https://example.com", "Example uses MIT licensing.")])
    assert evidence.citation_verified(result, "https://example.com", "Example uses MIT licensing.")
    assert not evidence.citation_verified({}, "https://example.com", "Example uses MIT licensing.")


@pytest.mark.parametrize("status,retryable", [(404, False), (429, True), (503, True)])
def test_retry_metadata_preserves_status_and_publisher_delay(monkeypatch, status, retryable):
    def fetch(*args):
        raise FeedError(
            "private response",
            reason="http_error",
            status=status,
            retryable=retryable,
            retry_after=600,
        )

    monkeypatch.setattr(evidence, "fetch_evidence_page", fetch)
    check = next(
        iter(evidence.verify_citations([("https://example.com", "A quote")])["checks"].values())
    )
    assert check["http_status"] == status and check["retry_after"] == 600
    assert evidence.citation_retryable(check) is retryable


@pytest.fixture
def evidence_cache(monkeypatch):
    from types import SimpleNamespace

    from devfeed_core import cache
    from devfeed_core.config import get_settings

    monkeypatch.setenv("DEVFEED_CACHE_ENABLED", "true")
    get_settings.cache_clear()
    values = {}
    redis = SimpleNamespace(
        get=lambda key: values.get(key),
        set=lambda key, value, ex: values.__setitem__(key, value),
        delete=lambda key: values.pop(key, None),
    )
    monkeypatch.setattr(cache, "get_cache", lambda: SimpleNamespace(namespace="test", redis=redis))
    return values


def test_evidence_cache_requires_fresh_conditional_validation(monkeypatch, evidence_cache):
    calls = []

    def fetch(url, timeout, **options):
        calls.append(options)
        if len(calls) == 1:
            return FetchResult(200, b"<p>Rust is supported.</p>", url, etag='"v1"')
        return FetchResult(304, b"", url)

    monkeypatch.setattr(evidence, "fetch_evidence_page", fetch)
    citations = [("https://example.com/docs", "Rust is supported.")]
    assert evidence.citation_verified(evidence.verify_citations(citations), *citations[0])
    assert evidence.citation_verified(evidence.verify_citations(citations), *citations[0])
    assert calls == [{}, {"etag": '"v1"'}]


@pytest.mark.parametrize("behavior", ["changed", "error", "redirect", "expired"])
def test_evidence_cache_never_approves_stale_or_redirected_content(
    monkeypatch, evidence_cache, behavior
):
    citations = [("https://example.com/docs", "Rust is supported.")]
    monkeypatch.setattr(
        evidence,
        "fetch_evidence_page",
        lambda url, timeout, **kw: FetchResult(200, b"<p>Rust is supported.</p>", url, etag='"v1"'),
    )
    evidence.verify_citations(citations)
    if behavior == "expired":
        evidence_cache.clear()

    def fetch(url, timeout, **options):
        if behavior == "error":
            raise FeedError("unavailable", reason="transport_error", retryable=True)
        if behavior == "redirect":
            return FetchResult(304, b"", "https://different.example.com/docs")
        if behavior == "expired":
            assert options == {}
        return FetchResult(200, b"<p>Only Python is supported.</p>", url, etag='"v2"')

    monkeypatch.setattr(evidence, "fetch_evidence_page", fetch)
    assert not evidence.citation_verified(evidence.verify_citations(citations), *citations[0])


def test_evidence_cache_respects_no_store(monkeypatch, evidence_cache):
    monkeypatch.setattr(
        evidence,
        "fetch_evidence_page",
        lambda url, timeout, **kw: FetchResult(
            200, b"<p>Rust.</p>", url, etag='"v1"', cache_control="no-store"
        ),
    )
    evidence.verify_citations([("https://example.com", "Rust.")])
    assert evidence_cache == {}


@pytest.mark.parametrize(
    "directive,seconds,reusable",
    [
        ("max-age=60", 60, True),
        ("max-age=0", 0, True),
        ("public, max-age=900", 300, True),
        ("no-cache", 300, False),
        ("private", 300, False),
    ],
)
def test_topic_reuse_metadata_respects_origin_cache_policy(
    monkeypatch, directive, seconds, reusable
):
    monkeypatch.setattr(
        evidence,
        "fetch_evidence_page",
        lambda url, timeout, **kw: FetchResult(
            200, b"<p>Supported public evidence.</p>", url, cache_control=directive
        ),
    )
    page = evidence.fetched_page("https://example.com/", 10)
    assert page["reuse_seconds"] == seconds
    assert page["reusable"] is reusable
    assert page["validated_at"]


URL = "https://project.example/docs"


QUOTE = "The project uses Rust."


@pytest.fixture
def transport(monkeypatch):
    settings = configuration(cache_enabled=True, cache_max_bytes=2048)
    redis = Mock()
    redis.get.return_value = None
    fetch = Mock(return_value=FetchResult(200, f"<p>{QUOTE}</p>".encode(), URL, etag='"v1"'))
    monkeypatch.setattr(evidence, "get_settings", lambda: settings)
    monkeypatch.setattr(cache, "get_cache", lambda: SimpleNamespace(namespace="test", redis=redis))
    monkeypatch.setattr(evidence, "fetch_evidence_page", fetch)
    return SimpleNamespace(redis=redis, fetch=fetch, settings=settings)


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        json.dumps(["unexpected array"]).encode(),
        json.dumps({"text": QUOTE, "final_url": URL, "content_hash": "hash"}).encode(),
        json.dumps({"text": 42, "final_url": URL, "content_hash": "hash", "etag": '"v1"'}).encode(),
        json.dumps({"text": "x" * 3000}).encode(),
    ],
)
def test_invalid_or_unvalidated_cache_entries_require_a_full_fresh_fetch(transport, raw):
    transport.redis.get.return_value = raw
    page = evidence.fetched_page(URL, 10)
    transport.fetch.assert_called_once()
    assert transport.fetch.call_args.args[0] == URL and transport.fetch.call_args.kwargs == {}
    assert page["text"] == QUOTE
    assert page["content_hash"] == hashlib.sha256(f"<p>{QUOTE}</p>".encode()).hexdigest()


def test_cache_read_failure_does_not_change_the_fresh_verification_result(transport):
    transport.redis.get.side_effect = ConnectionError("private cache endpoint")
    result = evidence.verify_citations([(URL, QUOTE)])
    assert evidence.citation_verified(result, URL, QUOTE)
    assert "private cache endpoint" not in str(result)
    transport.fetch.assert_called_once()
    transport.redis.set.assert_not_called()


@pytest.mark.parametrize("write", ["set", "delete"])
def test_optional_cache_write_failure_cannot_discard_fresh_public_evidence(transport, write):
    getattr(transport.redis, write).side_effect = ConnectionError("private cache endpoint")
    if write == "delete":
        transport.fetch.return_value = FetchResult(
            200, f"<p>{QUOTE}</p>".encode(), URL, cache_control="no-store"
        )
    result = evidence.verify_citations([(URL, QUOTE)])
    assert evidence.citation_verified(result, URL, QUOTE)
    getattr(transport.redis, write).assert_called_once()
    assert "private cache endpoint" not in str(result)


def test_unsolicited_not_modified_response_cannot_verify_content_without_a_cached_receipt(
    transport,
):
    transport.fetch.return_value = FetchResult(304, b"", URL)
    result = evidence.verify_citations([(URL, QUOTE)])
    check = result["checks"][evidence.citation_key(URL, QUOTE)]
    assert check["status"] == "unverified"
    assert check["reason"] == "invalid_evidence_revalidation" and check["retryable"] is True
    transport.redis.set.assert_not_called()


def test_cache_lookup_consuming_the_deadline_prevents_any_network_request(transport, monkeypatch):
    ticks = iter([100.0, 102.0])
    monkeypatch.setattr(evidence, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(FeedError) as error:
        evidence.fetched_page(URL, 1)
    assert error.value.reason == "verification_timeout" and error.value.retryable
    transport.fetch.assert_not_called()


def test_duplicate_citations_share_one_fetch_and_one_provenance_receipt(transport):
    result = evidence.verify_citations([(URL, QUOTE), (URL, QUOTE)])
    assert evidence.citation_verified(result, URL, QUOTE)
    assert len(result["checks"]) == 1
    transport.fetch.assert_called_once()


@pytest.mark.parametrize(
    "html",
    [
        "<p>The project<br/>uses Rust.</p>",
        f"<div hidden><b>Fabricated approval</b></div><p>{QUOTE}</p>",
        f"<p><em>{QUOTE}</unmatched></em></p>",
    ],
)
def test_visible_text_parsing_recovers_public_quotes_without_including_hidden_content(
    transport, html
):
    transport.fetch.return_value = FetchResult(200, html.encode(), URL)
    result = evidence.verify_citations([(URL, QUOTE), (URL, "Fabricated approval")])
    assert evidence.citation_verified(result, URL, QUOTE)
    assert not evidence.citation_verified(result, URL, "Fabricated approval")
    transport.fetch.assert_called_once()
