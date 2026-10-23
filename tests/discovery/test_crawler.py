"""Discovery crawling preserves admission, transport, and handoff boundaries."""

import copy
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import discovery, discovery_crawler, discovery_quality
from devfeed_core import discovery_crawler as crawl
from devfeed_core import discovery_crawler as crawler
from devfeed_core import research_verification as verification
from devfeed_core.discovery_crawler import feed_evidence
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.inference_errors import AnalysisError
from devfeed_core.models import (
    CandidateAssessment,
    CandidateFeed,
    ResearchVerificationJob,
    Source,
    SourceDiscoveryJob,
)
from devfeed_core.services import RecordNotFound
from devfeed_core.urls import validate_public_url
from protego import Protego

from tests.discovery.test_discovery_review_contracts_unit import (
    NOW,
    pending_candidate,
    pending_source,
)
from tests.discovery.test_source_discovery import feed as feed
from tests.support.source_readiness import NOW as CRAWL_NOW
from tests.support.source_readiness import configuration
from tests.support.taxonomy_command_helpers import NOW as RESEARCH_NOW
from tests.support.taxonomy_command_helpers import (
    metadata_job,
    proposal,
    relationship_job,
    relationship_proposal,
    topic,
)


@pytest.fixture
def feeds(monkeypatch):
    monkeypatch.setattr(discovery_crawler, "utcnow", lambda: NOW)
    monkeypatch.setattr(discovery_quality, "utcnow", lambda: NOW)
    items = "".join(
        f"<item><title>Database indexing lesson {i}</title>"
        f"<link>https://publisher.example/posts/{i}</link>"
        "<description>Measure query plans before changing database indexes.</description>"
        "<pubDate>Thu, 22 Oct 2026 08:00:00 GMT</pubDate></item>"
        for i in range(3)
    )
    rss = f'<rss version="2.0"><channel><title>Engineering</title>{items}</channel></rss>'.encode()
    entries = "".join(
        f"<entry><id>https://publisher.example/posts/{i}</id>"
        f"<title>Database indexing lesson {i}</title>"
        f'<link href="https://publisher.example/posts/{i}"/>'
        "<published>2026-10-22T08:00:00Z</published>"
        "<summary>Measure query plans before changing database indexes.</summary></entry>"
        for i in range(3)
    )
    atom = f'<feed xmlns="http://www.w3.org/2005/Atom"><id>https://publisher.example/</id><title>Engineering</title>{entries}</feed>'.encode()
    return [
        {
            "url": url,
            "method": "advertised",
            **feed_evidence(body, url, "https://publisher.example/"),
        }
        for url, body in [
            ("https://publisher.example/rss", rss),
            ("https://publisher.example/atom", atom),
        ]
    ]


@pytest.fixture
def runtime(monkeypatch, feeds):
    row = pending_candidate()
    source = pending_source(row)
    row.source_id = source.id
    job = SourceDiscoveryJob(
        id=uuid.uuid4(),
        candidate_id=row.id,
        stage="discover",
        status="queued",
        attempts=0,
        available_at=NOW,
        created_at=NOW,
        dispatched_at=NOW,
        result={"background": True, "assess_after": True},
    )
    selected = CandidateFeed(
        id=uuid.uuid4(),
        candidate_id=row.id,
        url=feeds[0]["url"],
        evidence=copy.deepcopy(feeds[0]),
        checked_at=NOW - timedelta(minutes=1),
    )
    read, write = Mock(), Mock()
    read.scalar.side_effect = [None, job, row]
    read.scalars.return_value = ["https://publisher.example/rss", None]
    read.get.side_effect = lambda model, key: (
        source if model is Source else selected if model is CandidateFeed else None
    )
    write.scalar.side_effect = [job, uuid.uuid4(), uuid.uuid4()]
    write.get.side_effect = read.get.side_effect
    activity = SimpleNamespace(open=False, calls=0, now=NOW)

    @contextmanager
    def begin():
        activity.open = True
        activity.calls += 1
        try:
            yield read if activity.calls == 1 else write
        finally:
            activity.open = False

    monkeypatch.setattr(discovery, "session_factory", lambda: SimpleNamespace(begin=begin))
    monkeypatch.setattr(discovery, "utcnow", lambda: activity.now)
    monkeypatch.setattr(
        discovery,
        "get_settings",
        lambda: configuration(ai_enabled=False, ai_server_overload_cooldown_seconds=240),
    )
    lookup = Mock(return_value=row)
    monkeypatch.setattr(discovery, "candidate", lookup)
    crawler = Mock(return_value={"feeds": copy.deepcopy(feeds), "attempts": []})
    monkeypatch.setattr(discovery, "discover", crawler)
    handoff = Mock()
    monkeypatch.setattr(discovery, "handoff", handoff)
    return SimpleNamespace(
        row=row,
        source=source,
        job=job,
        selected=selected,
        read=read,
        write=write,
        activity=activity,
        lookup=lookup,
        crawler=crawler,
        handoff=handoff,
    )


def assessment(runtime):
    runtime.job.stage = "assess"
    runtime.row.status, runtime.row.selected_feed_id = "ready", runtime.selected.id
    runtime.source.feed_url = runtime.selected.url
    sample = runtime.selected.evidence["sample"]
    return {
        "confidence": 0.95,
        "entries": [
            {
                "index": i,
                "relevance": "relevant",
                "depth": "substantial",
                "promotion": "editorial",
                "quote": entry["summary"],
            }
            for i, entry in enumerate(sample)
        ],
    }


def test_discovery_prefers_valid_atom_and_records_review_only_scores_outside_read_transaction(
    runtime, feeds
):
    def crawl(homepage, hints):
        assert not runtime.activity.open
        assert runtime.job.status == "running" and runtime.job.attempts == 1
        assert runtime.job.lease_until == NOW + timedelta(minutes=30)
        assert homepage == runtime.row.identity_url and hints == ["https://publisher.example/rss"]
        return {"feeds": copy.deepcopy(feeds), "attempts": []}

    runtime.crawler.side_effect = crawl
    started = Mock(
        side_effect=lambda _: (
            pytest.fail("Start callback held a database session") if runtime.activity.open else None
        )
    )
    result = discovery.run_one(on_start=started)
    assert result["status"] == "succeeded" and result["feeds_found"] == 2
    assert runtime.row.status == "ready" and runtime.row.selected_feed_id is not None
    scores = [args.args[0] for args in runtime.write.add.call_args_list]
    assert len(scores) == 2 and all(isinstance(row, CandidateAssessment) for row in scores)
    assert scores[0].evidence["sample"] == feeds[1]["sample"]
    assert all(not score.evidence["automatic_admission"] for score in scores)
    assert all(score.evidence["recommendation"] == "review" for score in scores)
    assert runtime.source.approval_status == "pending" and not runtime.source.enabled
    assert runtime.job.lease_token is None and runtime.job.finished_at == NOW
    assert runtime.job.result["background"] and runtime.job.result["assess_after"]
    runtime.handoff.assert_called_once_with(runtime.write, runtime.row)
    started.assert_called_once_with(
        {
            "candidate_id": runtime.row.id,
            "name": runtime.row.name,
            "stage": "discover",
            "attempt": 1,
        }
    )


@pytest.mark.parametrize("guard", ["busy", "no_due_job", "candidate_locked"])
def test_missing_admission_result_never_starts_external_work(runtime, guard):
    runtime.read.scalar.side_effect = {
        "busy": [uuid.uuid4()],
        "no_due_job": [None, None],
        "candidate_locked": [None, runtime.job, None],
    }[guard]
    assert discovery.run_one() is None
    runtime.crawler.assert_not_called()
    assert runtime.job.attempts == 0 and runtime.job.status == "queued"


def test_retry_budget_is_checked_before_starting_crawler(runtime):
    runtime.job.attempts = 5
    result = discovery.run_one()
    assert result == {"job_id": runtime.job.id, "status": "failed"}
    assert runtime.job.error == "attempts_exhausted" and runtime.job.finished_at == NOW
    assert runtime.row.status == "unresolved" and runtime.job.attempts == 5
    runtime.crawler.assert_not_called()


@pytest.mark.parametrize("reviewed", ["rejected", "linked", "admitted", "source_rejected"])
def test_queued_discovery_cannot_run_after_editorial_review(runtime, reviewed):
    if reviewed == "source_rejected":
        runtime.source.approval_status = "rejected"
    else:
        runtime.row.status = reviewed
    if reviewed in {"linked", "admitted"}:
        runtime.source.approval_status, runtime.source.enabled, runtime.source.feed_url = (
            "approved",
            True,
            runtime.selected.url,
        )
    result = discovery.run_one()
    assert result["status"] == "failed" and runtime.job.error == "candidate_reviewed"
    assert runtime.job.attempts == 0
    runtime.crawler.assert_not_called()


def test_background_delivery_yields_to_prepared_assessment_before_spending_attempt(
    runtime, monkeypatch
):
    monkeypatch.setattr(discovery, "get_settings", lambda: configuration(ai_enabled=True))
    runtime.read.scalar.side_effect = [None, runtime.job, uuid.uuid4()]
    assert discovery.run_one(target_job_id=runtime.job.id) is None
    assert runtime.job.status == "queued" and runtime.job.attempts == 0
    runtime.crawler.assert_not_called()


@pytest.mark.parametrize(
    "retryable,attempts,retry_after,expected_delay",
    [
        (True, 0, 0, 120),
        (True, 1, 600, 600),
        (True, 0, 200000, 86400),
        (False, 0, 0, None),
        (True, 4, 0, None),
    ],
)
def test_crawler_transport_errors_have_bounded_retries_and_safe_persisted_categories(
    runtime, retryable, attempts, retry_after, expected_delay
):
    runtime.job.attempts = attempts
    runtime.crawler.side_effect = FeedError(
        "https://user:secret@private.example/",
        reason="http_error",
        retryable=retryable,
        retry_after=retry_after,
        status=503,
    )
    result = discovery.run_one()
    assert result["error"] == runtime.job.error == "http_error"
    assert runtime.job.attempts == attempts + 1 and runtime.job.lease_token is None
    if expected_delay is not None:
        assert runtime.job.status == "queued" and runtime.row.status == "retry_wait"
        assert runtime.job.available_at == NOW + timedelta(seconds=expected_delay)
    else:
        assert runtime.job.status == "failed" and runtime.row.status == "unresolved"
    assert "secret" not in str(runtime.job.result)


@pytest.mark.parametrize(
    "code,retry_after,expected,delay",
    [
        ("codex_server_overloaded", 0, "codex_server_overloaded", 240),
        ("codex_timeout", 0, "codex_timeout", 120),
        ("codex_rate_limited", 200000, "codex_rate_limited", 86400),
        ("ai_not_configured", 0, "ai_not_configured", None),
        ("unexpected private server payload", 0, "assessment_failed", None),
    ],
)
def test_assessment_failure_codes_and_capacity_windows_do_not_leak_provider_payloads(
    runtime, code, retry_after, expected, delay
):
    assessment(runtime)
    assessor = Mock(side_effect=AnalysisError(code, retry_after=retry_after))
    result = discovery.run_one(allow_ai=True, assessor=assessor)
    assert result["error"] == expected
    assert runtime.job.status == ("queued" if delay else "failed")
    assert runtime.row.status == ("retry_wait" if delay else "ready")
    if delay:
        assert runtime.job.available_at == NOW + timedelta(seconds=delay)
    assert runtime.source.approval_status == "pending" and not runtime.source.enabled
    assert "private" not in str(runtime.job.result)


def test_supported_legacy_quality_assessment_is_only_a_recommendation(runtime):
    output = assessment(runtime)
    sample = copy.deepcopy(runtime.selected.evidence["sample"])

    def assess(supplied):
        assert not runtime.activity.open and supplied == sample
        return output

    result = discovery.run_one(allow_ai=True, assessor=assess)
    audit = runtime.write.add.call_args.args[0]
    assert result["status"] == "succeeded" and audit.feed_id == runtime.selected.id
    assert audit.evidence["recommendation"] == "recommend_approval"
    assert audit.evidence["score"] == 100 and not audit.evidence["automatic_admission"]
    assert runtime.source.approval_status == "pending" and not runtime.source.enabled
    runtime.handoff.assert_not_called()


def test_invalid_model_evidence_does_not_write_a_quality_assessment(runtime):
    output = assessment(runtime)
    output["entries"][0]["quote"] = "An invented passage absent from this feed."
    result = discovery.run_one(allow_ai=True, assessor=lambda _: output)
    assert result["status"] == "failed" and result["error"] == "assessment_or_discovery_failed"
    assert runtime.row.status == "ready" and runtime.source.approval_status == "pending"
    runtime.write.add.assert_not_called()


def test_expired_owner_cannot_apply_discovery_results_after_new_lease_was_issued(runtime):
    def crawl(*args):
        runtime.activity.now = NOW + timedelta(minutes=31)
        runtime.job.attempts += 1
        runtime.job.lease_token, runtime.job.lease_until = (
            uuid.uuid4(),
            runtime.activity.now + timedelta(minutes=30),
        )
        return {"feeds": [], "attempts": []}

    runtime.crawler.side_effect = crawl
    result = discovery.run_one()
    assert result == {"job_id": runtime.job.id, "status": "superseded"}
    assert runtime.job.status == "running" and runtime.job.finished_at is None
    assert runtime.row.selected_feed_id is None
    runtime.write.add.assert_not_called()


def test_source_deletion_during_network_work_is_reported_without_writing_results(runtime):
    runtime.lookup.side_effect = RecordNotFound("Discovery candidate not found")
    assert discovery.run_one() == {"job_id": runtime.job.id, "status": "deleted"}
    runtime.write.add.assert_not_called()


def test_rejection_during_discovery_wins_over_successful_crawl(runtime):
    def crawl(*args):
        runtime.row.status = runtime.row.approval_status = "rejected"
        runtime.source.approval_status = "rejected"
        runtime.source.reviewed_at, runtime.source.reviewed_by = NOW, "editor"
        return {"feeds": [], "attempts": []}

    runtime.crawler.side_effect = crawl
    result = discovery.run_one()
    assert result["error"] == "candidate_reviewed" and runtime.job.status == "failed"
    assert runtime.job.lease_token is None and runtime.source.approval_status == "rejected"
    runtime.write.add.assert_not_called()


def test_retryable_crawler_result_at_last_attempt_records_exhaustion(runtime):
    runtime.job.attempts = 4
    runtime.crawler.return_value = {"feeds": [], "attempts": [], "retryable": True}
    result = discovery.run_one()
    assert result["error"] == "attempts_exhausted" and result["status"] == "failed"
    assert runtime.row.status == "unresolved" and runtime.job.attempts == 5


HOME = "https://publisher.example/"


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=100.0)
    monkeypatch.setattr(
        crawler,
        "time",
        SimpleNamespace(
            monotonic=lambda: value.now,
            sleep=lambda delay: setattr(value, "now", value.now + delay),
        ),
    )
    monkeypatch.setattr(crawler, "utcnow", lambda: CRAWL_NOW)
    return value


@pytest.mark.parametrize("missing_status", [404, 410])
def test_absent_robots_file_allows_bounded_content_requests_without_repeating_lookup(
    monkeypatch, clock, missing_status
):
    calls = []

    def fetch(url, *_args, before_request, **_options):
        before_request(url)
        calls.append(url)
        if url.endswith("/robots.txt"):
            raise FeedError("No robots document", reason="http_error", status=missing_status)
        return FetchResult(200, b"<p>Public publication</p>", url, content_type="text/html")

    monkeypatch.setattr(crawler, "_fetch", fetch)
    client = crawler.CrawlSession()

    assert client.get(HOME).status == 200
    assert client.get(HOME + "blog").status == 200
    assert calls == [HOME + "robots.txt", HOME, HOME + "blog"]
    assert client.requests == 3


def test_elapsed_crawl_budget_preserves_already_verified_feeds_and_stops_fallbacks(
    monkeypatch, clock, feed
):
    calls = []

    def fetch(url, *_args, before_request, **_options):
        before_request(url)
        calls.append(url)
        if url.endswith("/robots.txt"):
            raise FeedError("No robots document", reason="http_error", status=404)
        return FetchResult(200, feed, url, content_type="application/rss+xml")

    monkeypatch.setattr(crawler, "_fetch", fetch)
    inspect = crawler.feed_evidence

    def verify_then_consume_budget(*args):
        evidence = inspect(*args)
        clock.now += 121  # Processing or a process pause consumes the remaining crawl budget.
        return evidence

    monkeypatch.setattr(crawler, "feed_evidence", verify_then_consume_budget)
    result = crawler.discover(HOME, [])

    assert [row["url"] for row in result["feeds"]] == [HOME]
    assert result["attempts"] == [{"url": HOME, "outcome": "valid_feed"}]
    assert calls == [HOME + "robots.txt", HOME]


def test_blog_feed_anchor_finds_atom_without_expanding_nested_navigation_or_guessed_paths(
    monkeypatch, clock
):
    atom = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><title>Engineering</title>'
        + "".join(
            f"<entry><id>{HOME}posts/{index}</id><title>Database design {index}</title>"
            f'<link href="{HOME}posts/{index}"/>'
            "<updated>2026-10-21T10:00:00Z</updated></entry>"
            for index in range(3)
        )
        + "</feed>"
    ).encode()
    pages = {
        HOME: b'<html><a href="/blog">Engineering blog</a></html>',
        HOME + "blog": b'<html><a href="/rss">Feed</a><a href="/news">More news</a></html>',
        HOME + "rss": atom,
    }
    calls = []

    def fetch(url, *_args, before_request, **_options):
        before_request(url)
        calls.append(url)
        if url.endswith("/robots.txt"):
            return FetchResult(200, b"User-agent: *\nAllow: /", url, content_type="text/plain")
        assert url in pages, f"Unexpected crawl expansion: {url}"
        return FetchResult(200, pages[url], url)

    monkeypatch.setattr(crawler, "_fetch", fetch)
    result = crawler.discover(HOME, [])

    assert calls == [HOME + "robots.txt", HOME, HOME + "blog", HOME + "rss"]
    assert len(result["feeds"]) == 1 and result["feeds"][0]["format"] == "atom"
    assert result["feeds"][0]["url"] == HOME + "rss"
    assert result["feeds"][0]["publisher_type"] == "direct"


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
    monkeypatch.setattr(verification, "utcnow", lambda: RESEARCH_NOW)
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
        created_at=RESEARCH_NOW - timedelta(minutes=19),
        available_at=RESEARCH_NOW - timedelta(minutes=18),
        finished_at=RESEARCH_NOW - timedelta(minutes=10),
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
        assert task.available_at == RESEARCH_NOW and task.finished_at is None
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
            "finished_at": str(RESEARCH_NOW - timedelta(minutes=10)),
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


def test_targeted_discovery_can_start_when_the_enabled_assessment_lane_has_no_waiting_work(
    runtime, monkeypatch
):
    current = runtime
    monkeypatch.setattr(discovery, "get_settings", lambda: configuration(ai_enabled=True))
    # No running discovery, the targeted job, no due assessment, and its candidate.
    current.read.scalar.side_effect = [None, current.job, None, current.row]

    result = discovery.run_one(target_job_id=current.job.id)

    assert result["status"] == "succeeded" and result["feeds_found"] == 2
    assert current.job.attempts == 1 and current.job.lease_token is None
    assert current.source.approval_status == "pending" and not current.source.enabled
    current.crawler.assert_called_once_with(
        current.row.identity_url, ["https://publisher.example/rss"]
    )
    current.handoff.assert_called_once_with(current.write, current.row)
