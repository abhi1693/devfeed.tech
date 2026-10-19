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


@pytest.mark.parametrize(
    "error", [FeedError("private transport failure"), ValueError("private page")]
)
def test_working_configured_feed_survives_unavailable_website(monkeypatch, error):
    def get(url):
        if url.endswith("/"):
            raise error
        return SimpleNamespace(body=b"feed", final_url="https://publisher.example/current.xml")

    monkeypatch.setattr(audit_module, "CrawlSession", lambda: SimpleNamespace(get=get))
    monkeypatch.setattr(audit_module, "feed_evidence", lambda *_: {})
    result = audit_module.audit("https://publisher.example/old.xml", "https://publisher.example/")
    assert result == {
        "feed_url": "https://publisher.example/old.xml",
        "website_url": "https://publisher.example/",
        "working_feeds": ["https://publisher.example/current.xml"],
        "configured_feed": "working",
        "website_check": "unavailable",
        "next_action": "review_verified_feed",
    }


def test_advertised_candidates_are_bounded_validated_and_deduplicated(monkeypatch):
    requested = []
    feeds = [
        "https://publisher.example/configured.xml",
        "http://127.0.0.1/private.xml",
        "https://publisher.example/invalid.xml",
        "https://publisher.example/absent.xml",
        "https://publisher.example/verified.xml",
        "https://publisher.example/extra.xml",
        "https://publisher.example/over-budget.xml",
    ]
    html = "".join(
        f'<link rel="alternate" type="application/rss+xml" href="{url}">' for url in feeds
    ).encode()

    def get(url):
        requested.append(url)
        if url.endswith("absent.xml"):
            raise FeedError("private upstream body", status=404)
        return SimpleNamespace(
            body=html if url.endswith("/") else b"feed",
            final_url=feeds[0] if url.endswith("extra.xml") else url,
        )

    def validate(_body, final_url, _website):
        if final_url.endswith("invalid.xml"):
            raise ValueError("private invalid feed contents")

    monkeypatch.setattr(audit_module, "CrawlSession", lambda: SimpleNamespace(get=get))
    monkeypatch.setattr(audit_module, "feed_evidence", validate)
    result = audit_module.audit(feeds[0], "https://publisher.example/")
    assert result["working_feeds"] == [feeds[0], feeds[4]]
    assert result["next_action"] == "review_verified_feed"
    assert requested == [
        feeds[0],
        "https://publisher.example/",
        feeds[0],
        feeds[2],
        feeds[3],
        feeds[4],
        feeds[5],
    ]
    assert "private" not in str(result)


def test_cli_emits_json_for_invalid_feed_without_raw_upstream_details(monkeypatch, capsys):
    import json
    import runpy
    import sys

    from devfeed_core import discovery_crawler

    def get(url):
        if url.endswith("/"):
            raise FeedError("private website response")
        return SimpleNamespace(body=b"not a feed", final_url=url)

    def invalid_feed(*_):
        raise ValueError("private invalid feed contents")

    monkeypatch.setattr(discovery_crawler, "feed_evidence", invalid_feed)
    monkeypatch.setattr(discovery_crawler, "CrawlSession", lambda: SimpleNamespace(get=get))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "source_feed_audit.py",
            "--feed-url",
            "https://publisher.example/old.xml",
            "--website-url",
            "https://publisher.example/",
        ],
    )
    runpy.run_path(str(audit_module.__file__), run_name="__main__")
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["configured_feed"] == "failed"
    assert result["failure_reason"] == "invalid_feed" and result["http_status"] is None
    assert result["website_check"] == "unavailable"
    assert result["working_feeds"] == []
    assert result["next_action"] == "editorial_decision_required"
    assert output.err == "" and "private" not in output.out
