"""Publisher redirect bounds and terminal transport recovery retain current article evidence."""

import uuid
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_aggregator import article_tasks as worker
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.models import ArticleEnrichmentJob
from devfeed_core.urls import fingerprint
from test_article_pages import PROSE
from test_runtime_article_worker_unit import article
from test_source_readiness_unit import NOW, configuration


class Factory:
    def __init__(self, session):
        self.session, self.open = session, False

    @contextmanager
    def begin(self):
        self.open = True
        try:
            yield self.session
        finally:
            self.open = False


def running(current):
    return ArticleEnrichmentJob(
        id=uuid.uuid4(),
        article_id=current.id,
        status="running",
        attempts=1,
        lease_token=uuid.uuid4(),
        lease_until=NOW + timedelta(minutes=4),
        created_at=NOW - timedelta(minutes=2),
        available_at=NOW - timedelta(minutes=1),
        result={},
        changed_fields=[],
    )


def redirect(url, target):
    return FetchResult(
        200,
        (
            f'<html><head><meta http-equiv="refresh" content="0;url={target}"></head>'
            f'<body><main>Redirecting to the publisher <a href="{target}">Continue</a>'
            "</main></body></html>"
        ).encode(),
        url,
        content_type="text/html",
    )


@pytest.mark.parametrize("path", ["valid", "loop", "too-many"])
def test_publisher_redirects_are_guarded_and_bounded(monkeypatch, path):
    current, session = article(), Mock()
    row, factory = running(current), Factory(session)
    urls = [
        current.canonical_url,
        "https://publisher.example/second",
        "https://publisher.example/third",
        "https://publisher.example/fourth",
    ]
    document = FetchResult(
        200,
        f"<html><article><h1>Publisher guide</h1><p>{PROSE * 4}</p></article></html>".encode(),
        urls[1],
        content_type="text/html",
    )
    responses = (
        [redirect(urls[0], urls[1]), document]
        if path == "valid"
        else [redirect(urls[0], urls[1]), redirect(urls[1], urls[0])]
        if path == "loop"
        else [redirect(urls[i], urls[i + 1]) for i in range(3)]
    )
    seen = []

    def fetch(url):
        assert not factory.open
        seen.append(url)
        return responses[len(seen) - 1]

    monkeypatch.setattr(worker, "fetch_article_page", fetch)
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)
    monkeypatch.setattr(worker, "get_settings", lambda: configuration(ai_enabled=False))
    monkeypatch.setattr(worker, "owned_job", Mock(return_value=row))
    monkeypatch.setattr(worker, "approved_sources", Mock(return_value=[uuid.uuid4()]))
    monkeypatch.setattr(worker, "attach_source_tags", Mock(return_value=0))
    session.scalar.return_value = current
    session.get.return_value = None
    session.scalars.return_value.all.return_value = []
    worker._enrich_claimed(factory, row.id, row.lease_token, current.id, urls[0], 0)
    assert seen == urls[: 2 if path != "too-many" else 3]
    assert current.canonical_url == urls[0]
    if path == "valid":
        assert row.status == "succeeded" and row.outcome == "enriched"
        assert row.result["publisher_redirects"] == urls[:2] and row.result["input_url"] == urls[0]
        content = session.add.call_args.args[0]
        assert content.url == urls[1] and PROSE.strip() in content.text
    else:
        assert row.status == "failed" and row.error == "Article lookup failed: page_unavailable"
        assert row.result == {} and current.summary is None
        session.add.assert_not_called()


@pytest.mark.parametrize(
    "state", ["retained-summary", "unapproved", "changed-url", "rejected", "lease-lost"]
)
def test_terminal_failure_reuses_summary_only_for_current_approved_articles(monkeypatch, state):
    current = article(summary="Publisher guidance explains concrete HTTP routing techniques.")
    row, session = running(current), Mock()
    factory = Factory(session)
    original = current.canonical_url
    if state == "changed-url":
        current.canonical_url = "https://publisher.example/replaced"
        current.url_hash = fingerprint(current.canonical_url)
    elif state == "rejected":
        current.review_status = "rejected"
    error = FeedError(
        "private publisher response", reason="http_error", status=404, retryable=False
    )
    monkeypatch.setattr(worker, "fetch_article_page", Mock(side_effect=error))
    monkeypatch.setattr(
        worker, "get_settings", lambda: configuration(ai_enabled=True, full_automation=True)
    )
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)
    monkeypatch.setattr(
        worker, "owned_job", Mock(return_value=None if state == "lease-lost" else row)
    )
    monkeypatch.setattr(
        worker,
        "approved_sources",
        Mock(return_value=[] if state == "unapproved" else [uuid.uuid4()]),
    )
    session.scalar.return_value = current
    session.get.return_value = None
    analyze = Mock()
    monkeypatch.setattr(worker, "request_analysis", analyze)
    worker._enrich_claimed(factory, row.id, row.lease_token, current.id, original, 0)
    if state == "retained-summary":
        analyze.assert_called_once_with(session, current.id, automatic=True)
    else:
        analyze.assert_not_called()
    if state == "lease-lost":
        assert row.status == "running" and row.error is None
    else:
        assert row.status == "failed" and row.error == "Article lookup failed: http_error"
        assert row.http_status == 404 and "private" not in row.error


def test_unclaimed_article_delivery_does_not_download_or_extract_anything(monkeypatch):
    factory, fetch = Factory(Mock()), Mock()
    monkeypatch.setattr(worker, "session_factory", lambda: factory)
    monkeypatch.setattr(worker, "claim_article", Mock(return_value=None))
    monkeypatch.setattr(worker, "fetch_article_page", fetch)
    worker.enrich_article(str(uuid.uuid4()))
    fetch.assert_not_called()
