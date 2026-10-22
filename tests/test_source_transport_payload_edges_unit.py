"""Untrusted source metadata and isolated solver receipts keep their transport boundaries."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpcore
import pytest
from devfeed_core import source_inspection, source_relevance
from devfeed_core.feeds import parser, queued_solver, validation
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.feeds.solver_providers import flaresolverr
from devfeed_core.feeds.solver_types import SolveRequest
from rq.job import JobStatus
from test_fetcher import use_pool
from test_source_discovery import feed as feed
from test_source_readiness_unit import NOW, configuration, profile, source
from test_source_solver import URL
from test_source_solver import queued as queued


def test_real_feed_parser_skips_unsafe_and_missing_media_urls_before_a_valid_enclosure():
    body = b"""<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
    <channel><title>Engineering</title><item><title>Database indexing</title>
    <link>https://publisher.example/database-indexing</link>
    <media:thumbnail url="http://127.0.0.1/private.png"/><media:thumbnail/>
    <enclosure url="https://publisher.example/cover.png" type="image/png"/>
    </item></channel></rss>"""

    result = parser.parse_feed(body, "https://publisher.example/feed", NOW, source_type="publisher")

    assert result.seen == 1 and result.skipped == 0
    assert result.entries[0].image_url == "https://publisher.example/cover.png"
    assert result.entries[0].source_metadata["image_url"] == result.entries[0].image_url


def test_unexpected_transport_failure_is_logged_and_propagated_without_solver_or_admission(
    monkeypatch, caplog
):
    failure = RuntimeError("transport adapter failed unexpectedly")
    monkeypatch.setattr(validation, "fetch_feed", Mock(side_effect=failure))
    solver = Mock()
    with pytest.raises(RuntimeError) as raised:
        validation.validate_feed(URL, source_type="publisher", solver=solver)
    assert raised.value is failure
    assert any(
        record.message == "feed_validation_error" and record.exc_info for record in caplog.records
    )
    solver.assert_not_called()


@pytest.mark.parametrize("missing_website", [False, True])
def test_complete_or_unlocated_source_profile_never_fetches_an_unnecessary_page(missing_website):
    values = {
        "description": "A software engineering publication.",
        "website_url": "https://publisher.example/",
        "logo_url": "https://publisher.example/logo.png",
        "image_url": "https://publisher.example/preview.png",
        "language": "en",
    }
    if missing_website:
        values = {"description": values["description"]}
    fetch = Mock(side_effect=AssertionError("unnecessary website lookup"))

    result = source_inspection.complete_website_profile(
        values, None if missing_website else values["website_url"], fetch=fetch
    )

    assert result["description"] == values["description"]
    assert result["website_url"] == values.get("website_url")
    assert result["logo_url"] == values.get("logo_url")
    fetch.assert_not_called()


def test_queued_solver_polls_unfinished_work_before_consuming_the_complete_feed_receipt(
    monkeypatch, queued, feed
):
    queued.job.get_status.side_effect = [JobStatus.STARTED, JobStatus.FINISHED] + [
        JobStatus.FINISHED
    ] * 2
    queued.job.return_value.return_value = {"body": base64.b64encode(feed).decode(), "url": URL}
    delays = []
    clock = [100.0]

    def sleep(delay):
        delays.append(delay)
        clock[0] += delay

    monkeypatch.setattr(
        queued_solver, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)
    )

    result = queued_solver.solve_feed(URL)

    assert result.body == feed and result.final_url == URL
    assert len(delays) == 1 and 0 < delays[0] <= 1
    queued.job.cancel.assert_not_called()
    queued.job.delete.assert_called_once_with()


@pytest.mark.parametrize("status", [403, True])
def test_successful_solver_http_envelope_cannot_hide_an_unsolved_or_untyped_page_status(
    monkeypatch, status
):
    content = json.dumps(
        {"status": "ok", "solution": {"url": URL, "response": "<p>Denied</p>", "status": status}}
    ).encode()
    use_pool(monkeypatch, [httpcore.Response(200, content=content)])
    provider = flaresolverr.FlareSolverr("http://solver.internal:8191")

    with pytest.raises(FeedError) as failure:
        provider.solve(SolveRequest(URL, 10, 1024, "feed"))

    assert failure.value.reason == "browser_challenge"


def test_malformed_cookie_items_are_ignored_without_losing_a_valid_scoped_cookie():
    headers = flaresolverr.image_headers(
        {
            "userAgent": "Browser/1.0",
            "cookies": [
                None,
                "untrusted scalar",
                {
                    "name": "session",
                    "value": "allowed",
                    "domain": "publication.example",
                    "path": "/",
                },
            ],
        },
        URL,
    )
    assert headers == {"User-Agent": "Browser/1.0", "Cookie": "session=allowed"}


@pytest.mark.parametrize("mode", ["automatic", "manual", "paused-ai", "undiscovered"])
def test_legacy_source_routing_requires_current_automation_ai_and_a_discovered_feed(
    monkeypatch, mode
):
    settings = configuration(ai_enabled=True, full_automation=mode != "manual")
    if mode == "paused-ai":
        settings.ai_enabled = False
    monkeypatch.setattr(source_relevance, "get_settings", lambda: settings)
    current = source(feed_url=None if mode == "undiscovered" else URL)
    legacy = profile(current, status="queued", operation="legacy", result={})

    assert source_relevance.requires_relevance(current, legacy) is (mode == "automatic")
