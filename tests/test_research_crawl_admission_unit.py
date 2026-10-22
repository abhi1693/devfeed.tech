"""Verification re-admission and discovery transport honor bounded independent work.

Verification rows are supplied repository results. Tests preserve actual prior
research receipts and exercise policy/state transitions, not SQL lane filtering.
Crawler transport is an external boundary; URL, robots and parser guards remain real.
"""

import copy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import discovery_crawler as crawl
from devfeed_core import research_verification as verification
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.models import ResearchVerificationJob
from devfeed_core.urls import validate_public_url
from protego import Protego
from taxonomy_command_helpers import (
    NOW,
    metadata_job,
    proposal,
    relationship_job,
    relationship_proposal,
    topic,
)
from test_source_readiness_unit import configuration


@pytest.mark.parametrize("relationships", [False, True])
@pytest.mark.parametrize("state", ["disabled", "lock-denied", "full", "new", "new-policy"])
def test_verification_admission_respects_lane_budget_and_keeps_previous_policy_audit(
    monkeypatch,
    relationships,
    state,
):
    settings = configuration(
        ai_enabled=state != "disabled",
        auto_approve_topics=not relationships,
        auto_approve_topic_relationships=relationships,
    )
    monkeypatch.setattr(verification, "get_settings", lambda: settings)
    monkeypatch.setattr(verification, "utcnow", lambda: NOW)
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    session.scalar.side_effect = [
        state != "lock-denied",
        (4 if relationships else 50) if state == "full" else 0,
    ]
    if relationships:
        focal, peer = topic(kind="library"), topic("JavaScript", kind="language")
        research = relationship_job(focal, peer)
        relationship_proposal(focal, peer, research)
    else:
        current = proposal()
        research = metadata_job(current)
    task = ResearchVerificationJob(
        id=research.id,
        relationships=relationships,
        status="succeeded",
        attempts=2,
        created_at=NOW - timedelta(minutes=19),
        available_at=NOW - timedelta(minutes=18),
        finished_at=NOW - timedelta(minutes=10),
        outcome="review_required",
        usage={"attempts": {"2": {"inputTokens": 300}}},
        error=None,
    )
    old_policy = "previous-topic-check/previous-relationship-check"
    research.result = {**research.result, "verification_policy_version": old_policy}
    before = copy.deepcopy(research.result)
    session.get.return_value = task if state == "new-policy" else None
    session.scalars.return_value.all.return_value = [research]
    assert verification.schedule_verification(factory) == int(state in {"new", "new-policy"})
    if state == "disabled":
        factory.begin.assert_not_called()
    elif state in {"lock-denied", "full"}:
        session.add.assert_not_called()
        session.scalars.assert_not_called()
    elif state == "new":
        admitted = session.add.call_args.args[0]
        assert admitted.id == research.id and admitted.relationships is relationships
    else:
        assert task.status == "queued" and task.attempts == 0
        assert task.available_at == NOW and task.finished_at is None
        assert task.lease_token is task.lease_until is task.dispatched_at is None
        assert task.usage == {} and task.outcome is task.error is None
        [cycle] = research.result["verification_cycles"]
        assert cycle == {
            "policy": old_policy,
            "status": "succeeded",
            "outcome": "review_required",
            "attempts": 2,
            "usage": {"attempts": {"2": {"inputTokens": 300}}},
            "error": None,
            "finished_at": str(NOW - timedelta(minutes=10)),
        }
    if state in {"new", "new-policy"}:
        assert research.result["verification_policy_version"] == verification.POLICY_VERSION
        assert all(
            research.result[key] == value
            for key, value in before.items()
            if key != "verification_policy_version"
        )
        assert research.status == "succeeded" and research.outcome == "enriched"


def test_robots_dependency_failure_is_retryable_without_spending_page_transport(monkeypatch):
    client = crawl.CrawlSession()
    unavailable = FeedError(
        "Publisher unavailable", reason="http_503", status=503, retryable=True, retry_after=23
    )
    client.get = Mock(side_effect=unavailable)
    with pytest.raises(FeedError) as refused:
        client.before_request("https://publisher.example/blog")
    assert refused.value.reason == "robots_unavailable" and refused.value.retryable
    assert refused.value.retry_after == 23 and refused.value.__cause__ is unavailable
    assert client.requests == 0 and client.robots == {}


@pytest.mark.parametrize("budget", [False, True])
def test_robots_request_rate_is_obeyed_without_sleeping_past_the_global_deadline(
    monkeypatch, budget
):
    clock = SimpleNamespace(now=102.0)
    sleep = Mock(side_effect=lambda seconds: setattr(clock, "now", clock.now + seconds))
    monkeypatch.setattr(crawl, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    client = crawl.CrawlSession()
    client.robots["https://publisher.example"] = Protego.parse(
        "User-agent: DevFeed\nRequest-rate: 2/10\n"
    )
    client.last_request["publisher.example"] = 100.0
    if budget:
        client.deadline = 104.0
        with pytest.raises(FeedError) as limited:
            client.before_request("https://publisher.example/blog")
        assert limited.value.reason == "discovery_budget" and limited.value.retryable
        sleep.assert_not_called()
        assert client.requests == 0
    else:
        client.before_request("https://publisher.example/blog")
        sleep.assert_called_once_with(3.0)
        assert client.requests == 1 and client.last_request["publisher.example"] == 105.0


@pytest.mark.parametrize("redirect", [False, True])
def test_rate_limited_origin_and_redirect_origin_are_fenced_for_the_rest_of_the_crawl(
    monkeypatch, redirect
):
    monkeypatch.setattr(crawl, "time", SimpleNamespace(monotonic=lambda: 100.0, sleep=Mock()))
    client = crawl.CrawlSession()
    client.robots = {"https://publisher.example": None, "https://cdn.example": None}
    error = FeedError(
        "Publisher rate limit", reason="http_429", status=429, retryable=True, retry_after=90
    )

    def fetch(url, *_args, **options):
        options["before_request"](url)
        if redirect:
            options["before_request"]("https://cdn.example/feed")
        raise error

    monkeypatch.setattr(crawl, "_fetch", fetch)
    with pytest.raises(FeedError) as raised:
        client.get("https://publisher.example/feed")
    assert raised.value is error
    expected = (
        {"https://publisher.example", "https://cdn.example"}
        if redirect
        else {"https://publisher.example"}
    )
    assert set(client.blocked) == expected
    requests = client.requests
    for origin in expected:
        with pytest.raises(FeedError) as blocked:
            client.before_request(origin + "/another-page")
        assert blocked.value is error
    assert client.requests == requests


def test_html_discovery_honors_first_safe_base_and_ignores_unsafe_or_missing_targets():
    parser = crawl.Links("https://publisher.example/blog/")
    parser.feed(
        '<a>missing href</a><base href="https://publisher.example/engineering/">'
        '<base href="https://attacker.example/">'
        '<link rel="alternate" type="application/atom+xml" href="feed.xml">'
        '<a href="http://127.0.0.1/private">unsafe</a>'
        '<a href="https://other.example/blog">unrelated page</a>'
    )
    assert parser.base == "https://publisher.example/engineering/"
    assert parser.feeds == ["https://publisher.example/engineering/feed.xml"]
    assert parser.pages == []


def test_discovery_deduplicates_hints_and_reports_unsafe_targets_without_fetching_them(monkeypatch):
    class Transport:
        requests, deadline = 0, float("inf")

        def get(self, url):
            validate_public_url(url)
            self.requests += 1
            return FetchResult(
                status=200, final_url=url, body=b"<html><title>Publisher</title></html>"
            )

    transport = Transport()
    monkeypatch.setattr(crawl, "CrawlSession", lambda: transport)
    home = "https://publisher.example/engineering/"
    result = crawl.discover(home, [home, home, "http://127.0.0.1/private"])
    assert result["feeds"] == [] and result["retryable"] is False
    assert result["attempts"][0] == {"url": home, "outcome": "not_feed"}
    assert sum(row["url"] == home for row in result["attempts"]) == 1
    assert {"url": "http://127.0.0.1/private", "outcome": "unsafe_url"} in result["attempts"]
    assert result["requests"] == transport.requests == len(result["attempts"]) - 1
