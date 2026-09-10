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
