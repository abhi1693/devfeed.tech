"""Operator analysis admission and dispatch lifetime, with repository/service boundaries."""

import copy
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_cli import editorial
from devfeed_core.config import get_settings
from devfeed_core.models import ArticleAnalysisJob
from devfeed_core.services import OperationConflict, RecordNotFound
from test_article_lifecycle import facts
from test_feed_snapshot_contracts_unit import NOW


@pytest.fixture
def operation(monkeypatch):
    article, values = facts()
    article.origins[0].source.feed_url = "https://publisher.example/feed"
    article.origins[0].source.enabled = True
    article.origins[0].entry_key = str(article.id)
    article.origins[0].original_url = article.canonical_url
    original = values["analysis"]
    original.created_at = NOW - timedelta(minutes=10)
    original.available_at = NOW - timedelta(minutes=9)
    original.finished_at = NOW - timedelta(minutes=3)
    queued = ArticleAnalysisJob(
        id=uuid.uuid4(),
        article_id=article.id,
        status="queued",
        attempts=0,
        input_hash=original.input_hash,
        input_snapshot=copy.deepcopy(original.input_snapshot),
        catalog_hash=original.catalog_hash,
        catalog_snapshot=copy.deepcopy(original.catalog_snapshot),
        editorial_revision=article.editorial_revision,
        prompt_version=original.prompt_version,
        result={},
        created_at=NOW,
        available_at=NOW + timedelta(minutes=2),
        model="test-model",
        dispatched_at=None,
    )
    previous = ArticleAnalysisJob(
        id=uuid.uuid4(),
        article_id=article.id,
        status="failed",
        attempts=3,
        input_hash=original.input_hash,
        input_snapshot=copy.deepcopy(original.input_snapshot),
        catalog_hash=original.catalog_hash,
        catalog_snapshot=copy.deepcopy(original.catalog_snapshot),
        editorial_revision=article.editorial_revision,
        prompt_version=original.prompt_version,
        result={},
        created_at=NOW - timedelta(minutes=10),
        available_at=NOW - timedelta(minutes=5),
        finished_at=NOW - timedelta(minutes=3),
        error="provider_unavailable",
        model="test-model",
    )
    session = Mock()
    session.get.return_value = previous
    events = []

    @contextmanager
    def scope():
        events.append("enter")
        try:
            yield session
        finally:
            events.append("exit")

    factory = Mock(side_effect=scope)
    factory.begin.side_effect = scope
    monkeypatch.setattr(editorial, "session_factory", lambda: factory)
    monkeypatch.setattr(editorial, "utcnow", lambda: NOW)
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    requester = Mock(return_value=queued)
    monkeypatch.setattr(editorial, "request_analysis", requester)
    return SimpleNamespace(
        article=article,
        completed=original,
        queued=queued,
        previous=previous,
        session=session,
        factory=factory,
        events=events,
        requester=requester,
    )


def existing_job(operation, status):
    if status is None:
        return None
    if status == "succeeded":
        return operation.completed
    if status == "failed":
        return operation.previous
    job = operation.queued
    if status == "running":
        job.status, job.attempts = "running", 1
        job.created_at = NOW - timedelta(minutes=2)
        job.available_at = NOW - timedelta(minutes=1)
        job.lease_token, job.lease_until = uuid.uuid4(), NOW + timedelta(minutes=3)
    return job


@pytest.mark.parametrize("action", ["analyze", "analysis-retry", "analysis-backfill"])
def test_disabled_analysis_fails_before_opening_a_database_scope(operation, monkeypatch, action):
    monkeypatch.setattr(get_settings(), "ai_enabled", False)
    handler = editorial.analysis_backfill if action == "analysis-backfill" else editorial.analyze
    with pytest.raises(OperationConflict, match="Configure"):
        handler(SimpleNamespace(action=action, id=operation.article.id))
    assert operation.events == []
    operation.requester.assert_not_called()


@pytest.mark.parametrize("retry", [False, True])
def test_analysis_admission_targets_the_article_and_does_not_dispatch_implicitly(operation, retry):
    args = SimpleNamespace(
        action="analysis-retry" if retry else "analyze",
        id=operation.previous.id if retry else operation.article.id,
        force=False,
    )
    result = editorial.analyze(args)
    operation.requester.assert_called_once_with(operation.session, operation.article.id)
    assert result["id"] == operation.queued.id and result["article_id"] == operation.article.id
    assert result["status"] == "queued" and operation.events == ["enter", "exit"]


@pytest.mark.parametrize("status", [None, "queued", "running", "succeeded"])
def test_retry_admission_rejects_missing_or_nonfailed_jobs_without_requesting_work(
    operation, status
):
    old = existing_job(operation, status)
    operation.session.get.return_value = old
    expected = RecordNotFound if status is None else OperationConflict
    with pytest.raises(expected):
        editorial.analyze(
            SimpleNamespace(
                action="analysis-retry", id=old.id if old is not None else uuid.uuid4(), force=False
            )
        )
    operation.requester.assert_not_called()


def test_explicit_force_dispatch_starts_after_the_admission_scope_has_closed(
    operation, monkeypatch
):
    def dispatch(identifier):
        assert operation.events == ["enter", "exit"]
        assert identifier == operation.queued.id
        return {"id": identifier, "dispatched": True}

    monkeypatch.setattr(editorial, "dispatch_analysis", dispatch)
    result = editorial.analyze(
        SimpleNamespace(action="analyze", id=operation.article.id, force=True)
    )
    assert result == {"id": operation.queued.id, "dispatched": True}


@pytest.mark.parametrize("dispatch", [False, True])
def test_backfill_preserves_scan_cursor_and_dispatches_only_admitted_jobs_after_scope_close(
    operation, monkeypatch, dispatch
):
    operation.article.review_status = "pending"
    after = uuid.UUID(int=1)
    next_after = str(operation.article.id)
    scan = Mock(return_value=([operation.queued], 10, next_after))
    monkeypatch.setattr(editorial, "backfill_analyses", scan)

    def deliver(identifier):
        assert operation.events == ["enter", "exit"]
        return {"id": identifier, "dispatched": True}

    delivery = Mock(side_effect=deliver)
    monkeypatch.setattr(editorial, "dispatch_analysis", delivery)
    args = SimpleNamespace(limit=10, after=after, force=True, dispatch=dispatch)
    result = editorial.analysis_backfill(args)
    scan.assert_called_once_with(operation.session, 10, after=after, force=True)
    assert result["scanned"] == 10 and result["queued"] == 1 and result["next_after"] == next_after
    assert result["jobs"][0]["id"] == operation.queued.id
    if dispatch:
        delivery.assert_called_once_with(operation.queued.id)
        assert result["jobs"][0]["dispatched"]
    else:
        delivery.assert_not_called()


def test_routine_job_projection_omits_private_source_and_catalog_snapshots(operation):
    job = operation.queued
    result = editorial.job_view(job)
    assert result["input_hash"] == job.input_hash and result["prompt_version"] == job.prompt_version
    assert {"input_snapshot", "catalog_snapshot", "usage", "lease_token"}.isdisjoint(result)
    assert result["result"] == {} and result["id"] == job.id


@pytest.mark.parametrize("status", [None, "running", "succeeded", "failed"])
def test_dispatch_requires_an_existing_queued_job_before_touching_redis(
    operation, monkeypatch, status
):
    job = existing_job(operation, status)
    operation.session.scalar.return_value = job
    broker = Mock(side_effect=AssertionError("Redis must not be touched"))
    monkeypatch.setattr(editorial, "get_queue", broker)
    with pytest.raises(RecordNotFound if status is None else OperationConflict):
        editorial.dispatch_analysis(job.id if job is not None else uuid.uuid4())
    broker.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_dispatch_closes_redis_after_success_or_failure_and_uses_the_analysis_lane(
    operation, monkeypatch, fails
):
    job = operation.queued
    operation.session.scalar.return_value = operation.session.get.return_value = job
    queue = Mock()
    monkeypatch.setattr(editorial, "get_queue", Mock(return_value=queue))

    def publish(factory, selected, batch, now, **options):
        assert operation.events == ["enter", "exit"]
        assert factory is operation.factory and selected is queue
        assert (batch, now, options) == (1, NOW, {"job_id": job.id, "kind": "analysis"})
        assert job.available_at == NOW and job.dispatched_at is None
        if fails:
            raise RuntimeError("broker unavailable")
        job.dispatched_at = now
        return 1

    monkeypatch.setattr(editorial, "dispatch_jobs", publish)
    if fails:
        with pytest.raises(RuntimeError, match="broker unavailable"):
            editorial.dispatch_analysis(job.id)
    else:
        result = editorial.dispatch_analysis(job.id)
        assert result["id"] == job.id and result["dispatched_at"] == NOW
    assert job.available_at == NOW and job.dispatched_at == (None if fails else NOW)
    queue.connection.close.assert_called_once_with()
    editorial.get_queue.assert_called_once_with("article-analysis")
