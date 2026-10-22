"""Public evidence remains independently verified when optional cache data fails."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import cache
from devfeed_core import research_evidence as evidence
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from test_source_readiness_unit import configuration

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
