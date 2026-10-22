"""Discovery workflow over preloaded reads; SQL admission and locking stay integration contracts."""

import copy
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import discovery, discovery_crawler, discovery_quality
from devfeed_core.discovery_crawler import feed_evidence
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.inference_errors import AnalysisError
from devfeed_core.models import CandidateAssessment, CandidateFeed, Source, SourceDiscoveryJob
from devfeed_core.services import RecordNotFound
from test_discovery_review_contracts_unit import NOW, pending_candidate, pending_source
from test_source_readiness_unit import configuration


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
