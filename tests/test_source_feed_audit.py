"""Publisher maintenance must not guess feeds, expose remote errors or alter records."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from devfeed_core.feeds.fetcher import FeedError

spec = importlib.util.spec_from_file_location(
    "source_feed_audit", Path(__file__).parents[1] / "scripts/source_feed_audit.py"
)
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


def test_missing_feed_requires_editorial_decision_without_guessing(monkeypatch):
    requested = []

    def get(url):
        requested.append(url)
        if url.endswith("/old.xml"):
            raise FeedError("private upstream body", status=404, reason="http_error")
        return SimpleNamespace(body=b"<h1>Publisher</h1>", final_url=url)

    monkeypatch.setattr(audit_module, "CrawlSession", lambda: SimpleNamespace(get=get))
    result = audit_module.audit("https://publisher.example/old.xml", "https://publisher.example/")
    assert result["http_status"] == 404
    assert result["next_action"] == "editorial_decision_required"
    assert len(requested) == 2
    assert "private" not in str(result)


def test_only_publisher_advertised_valid_feeds_are_proposed(monkeypatch):
    def get(url):
        body = (
            b'<link rel="alternate" type="application/rss+xml" href="/verified.xml">'
            if url.endswith("/")
            else b"feed"
        )
        if url.endswith("old.xml"):
            raise FeedError("missing", status=404)
        return SimpleNamespace(body=body, final_url=url)

    monkeypatch.setattr(audit_module, "CrawlSession", lambda: SimpleNamespace(get=get))
    monkeypatch.setattr(audit_module, "feed_evidence", lambda *_: {})
    result = audit_module.audit("https://publisher.example/old.xml", "https://publisher.example/")
    assert result["working_feeds"] == ["https://publisher.example/verified.xml"]
    assert result["next_action"] == "review_verified_feed"


def test_audit_rejects_private_targets_before_transport():
    with pytest.raises(ValueError):
        audit_module.audit("http://127.0.0.1/feed", "https://publisher.example/")
