"""Runtime invariant failures retain retries, redaction and lease ownership."""

import copy
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import ANY, Mock

import pytest
from devfeed_aggregator import tasks, topic_analysis_tasks
from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.models import IngestionJob, Source, TopicAnalysisJob, TopicProposal, utcnow
from devfeed_core.schemas import SourceType


@pytest.mark.parametrize("attempts", [1, 3])
@pytest.mark.parametrize("lost_lease", [False, True])
def test_missing_ingestion_source_uses_runtime_failure_path(monkeypatch, attempts, lost_lease):
    now, token, identifier = utcnow(), uuid.uuid4(), uuid.uuid4()
    job = IngestionJob(
        id=identifier,
        status="running",
        attempts=attempts,
        source_id=uuid.uuid4(),
        lease_token=token,
        lease_until=now + timedelta(minutes=5),
    )
    source = Source(
        id=job.source_id,
        enabled=True,
        approval_status="approved",
        poll_interval_seconds=3600,
        consecutive_failures=0,
    )
    apply_session = Mock()
    apply_session.scalar.return_value = None
    failure_session = Mock()
    failure_session.scalar.return_value = source
    sessions = iter([apply_session, failure_session])

    @contextmanager
    def transaction():
        yield next(sessions)

    factory = SimpleNamespace(begin=transaction)
    ownership = Mock(side_effect=[job, None if lost_lease else job])
    store = Mock()
    monkeypatch.setattr(tasks, "owned_job", ownership)
    monkeypatch.setattr(tasks, "store_entries", store)
    monkeypatch.setattr(
        tasks, "fetch_feed", lambda *_args: FetchResult(304, b"", "https://example.com/rss")
    )

    tasks._ingest_claimed(
        factory,
        identifier,
        token,
        "https://example.com/rss",
        None,
        None,
        time.perf_counter(),
        SourceType("publisher"),
    )

    store.assert_not_called()
    assert ownership.call_count == 2
    assert all(call.args[-1] == token for call in ownership.call_args_list)
    assert job.attempts == attempts
    if lost_lease:
        assert job.status == "running" and job.lease_token == token and job.error is None
        assert source.last_error is None
        failure_session.scalar.assert_not_called()
    else:
        assert job.status == ("queued" if attempts < 3 else "failed")
        assert job.error == source.last_error == "Ingestion error: RuntimeError"
        assert job.lease_token is job.lease_until is None
        assert source.last_success_at is None
        if attempts < 3:
            assert job.available_at > now
        else:
            assert job.finished_at >= now and source.consecutive_failures == 1


@pytest.fixture
def topic_worker(monkeypatch):
    proposed = {"name": "Example", "slug": "example", "kind": "technology"}
    proposal = TopicProposal(
        id=uuid.uuid4(), status="pending", proposed=proposed, evidence=[], origin="import"
    )
    job = TopicAnalysisJob(
        id=uuid.uuid4(),
        proposal_id=proposal.id,
        input_snapshot={"topic": proposed, "evidence": []},
        input_hash=snapshot_hash(proposed),
        status="queued",
        attempts=0,
        available_at=utcnow() - timedelta(seconds=1),
        result={},
        usage={},
        outcome=None,
        error=None,
        lease_token=None,
        lease_until=None,
        finished_at=None,
    )
    state = SimpleNamespace(proposal=proposal, lease_owned=True)
    session = Mock()
    session.scalar.side_effect = lambda *_args: job if job.status == "queued" else state.proposal
    session.get.return_value = proposal

    @contextmanager
    def transaction():
        # Like a real unit of work, failed follow-up work rolls back completion
        # before the worker checks the lease in its failure transaction.
        before = {
            key: copy.deepcopy(value) for key, value in vars(job).items() if not key.startswith("_")
        }
        try:
            yield session
        except Exception:
            for key, value in before.items():
                setattr(job, key, value)
            raise

    settings = get_settings().model_copy(
        update={"ai_enabled": True, "ai_bounded_topics_enabled": False, "codex_model": "test"}
    )
    result = {
        "outcome": "ready",
        "kind": None,
        "description": "Supported developer technology.",
        "aliases": [],
        "keywords": [],
        "website_url": None,
        "logo_url": None,
        "facts": [],
        "sources": [],
        "reasons": [],
    }
    client = Mock(web_search_count=0)
    client.complete.return_value = result
    monkeypatch.setattr(topic_analysis_tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(
        topic_analysis_tasks, "session_factory", lambda: SimpleNamespace(begin=transaction)
    )
    monkeypatch.setattr(topic_analysis_tasks, "CodexClient", lambda *_args: client)
    monkeypatch.setattr(topic_analysis_tasks, "proposal_allowed", lambda *_args: True)
    monkeypatch.setattr(topic_analysis_tasks, "lock_topics", Mock())
    monkeypatch.setattr(topic_analysis_tasks, "apply_topic_research", Mock(return_value="enriched"))
    monkeypatch.setattr(topic_analysis_tasks, "auto_approve_research", Mock())
    monkeypatch.setattr(topic_analysis_tasks, "record_attempt", Mock())

    def owned(_session, _model, identifier, token):
        assert identifier == job.id
        return (
            job
            if state.lease_owned and job.status == "running" and job.lease_token == token
            else None
        )

    monkeypatch.setattr(topic_analysis_tasks, "owned_job", owned)
    followup = Mock()
    monkeypatch.setattr(topic_analysis_tasks, "queue_relationships_after_enrichment", followup)
    return SimpleNamespace(job=job, state=state, followup=followup, client=client)


def test_enriched_topic_queues_relationships_only_with_a_current_proposal(topic_worker):
    topic_analysis_tasks._analyze(topic_worker.job.id)
    job = topic_worker.job
    assert job.status == "succeeded" and job.outcome == "enriched"
    assert job.lease_token is job.lease_until is None
    topic_worker.followup.assert_called_once_with(ANY, topic_worker.state.proposal, job)


def test_missing_topic_proposal_is_superseded_without_relationship_work(topic_worker):
    topic_worker.state.proposal = None
    topic_analysis_tasks._analyze(topic_worker.job.id)
    assert topic_worker.job.status == "succeeded" and topic_worker.job.outcome == "superseded"
    topic_worker.followup.assert_not_called()


@pytest.mark.parametrize("attempts", [0, 2])
@pytest.mark.parametrize("lost_lease", [False, True])
def test_topic_followup_runtime_failure_rolls_back_and_preserves_owner(
    topic_worker, attempts, lost_lease
):
    topic_worker.job.attempts = attempts

    def fail_followup(*_args):
        topic_worker.state.lease_owned = not lost_lease
        raise RuntimeError("Private dependency detail")

    topic_worker.followup.side_effect = fail_followup
    topic_analysis_tasks._analyze(topic_worker.job.id)
    job = topic_worker.job
    assert job.attempts == attempts + 1 and job.outcome is None
    if lost_lease:
        assert job.status == "running" and job.lease_token is not None
        assert job.error is None
    else:
        assert job.status == ("queued" if attempts == 0 else "failed")
        assert job.error == "analysis_dependency_failure"
        assert job.lease_token is job.lease_until is None
