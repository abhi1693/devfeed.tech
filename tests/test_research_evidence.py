import hashlib

import pytest
from devfeed_core import research_evidence as evidence
from devfeed_core.feeds.fetcher import FeedError, FetchResult


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
