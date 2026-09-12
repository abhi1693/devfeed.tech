import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator import article_tasks, image_tasks, source_tasks, worker
from devfeed_aggregator.solver_jobs import defer_to_solver
from devfeed_core.config import SolverService, get_settings
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.job_dispatch import dispatch_jobs
from devfeed_core.models import (
    Article,
    ArticleEnrichmentJob,
    ArticleImageJob,
    Source,
    SourceEnrichmentJob,
    utcnow,
)
from devfeed_core.services import OperationConflict


@pytest.mark.parametrize(
    "model,handler,fetch_name",
    [
        (ArticleEnrichmentJob, article_tasks, "fetch_article_page"),
        (ArticleImageJob, image_tasks, "fetch_page"),
    ],
)
def test_actual_article_and_image_handlers_handoff_without_using_attempts(
    monkeypatch, model, handler, fetch_name
):
    monkeypatch.setattr(get_settings(), "solver_queue_enabled", True)
    token = uuid.uuid4()
    job = model(
        id=uuid.uuid4(), article_id=uuid.uuid4(), status="running", attempts=3, lease_token=token
    )

    @contextmanager
    def begin():
        yield SimpleNamespace()

    factory = SimpleNamespace(begin=begin)
    monkeypatch.setattr(handler, "session_factory", lambda: factory)
    claim = "claim_article" if model is ArticleEnrichmentJob else "claim_image"
    monkeypatch.setattr(handler, claim, lambda *a: (job, "https://publisher.example/article"))
    monkeypatch.setattr(handler, "owned_job", lambda *a: job)

    def challenge(url):
        raise FeedError("challenge", reason="browser_challenge", status=403)

    monkeypatch.setattr(handler, fetch_name, challenge)
    if model is ArticleEnrichmentJob:
        handler.enrich_article(str(job.id))
    else:
        handler.enrich_image(str(job.id))
    assert job.requires_solver and job.status == "queued"
    assert job.attempts == 2 and job.lease_token is None and job.dispatched_at is None
    assert job.http_status == 403


@pytest.mark.parametrize(
    "enabled,reason,expected",
    [
        (False, "browser_challenge", False),
        (True, "http_error", False),
        (True, "browser_challenge", True),
    ],
)
def test_handoff_only_for_recognized_challenges(monkeypatch, enabled, reason, expected):
    monkeypatch.setattr(get_settings(), "solver_queue_enabled", enabled)
    job = SourceEnrichmentJob(status="running", attempts=1)
    assert defer_to_solver(job, FeedError("x", reason=reason), "safe error") is expected
    assert (job.status == "queued") is expected


def test_solver_workers_do_not_handoff_their_own_failures_again(monkeypatch):
    monkeypatch.setattr(get_settings(), "solver_queue_enabled", True)
    monkeypatch.setattr(
        get_settings(),
        "solver_services",
        [SolverService(provider="flaresolverr", url="http://solver.internal")],
    )
    job = SourceEnrichmentJob(status="running", attempts=1, requires_solver=True)
    assert not defer_to_solver(job, FeedError("x", reason="browser_challenge"), "safe error")
    assert job.attempts == 1


@pytest.mark.parametrize("queue", ["all", "background", "analysis", "ingestion"])
def test_normal_workers_cannot_accidentally_use_remote_solver(monkeypatch, queue):
    monkeypatch.setattr(
        get_settings(),
        "solver_services",
        [SolverService(provider="flaresolverr", url="http://solver.internal")],
    )
    with pytest.raises(OperationConflict, match="Only the dedicated solver worker"):
        worker.run(queue_name=queue)


@pytest.mark.integration
@pytest.mark.parametrize(
    "kind,model",
    [
        ("article-enrichment", ArticleEnrichmentJob),
        ("images", ArticleImageJob),
        ("source-enrichment", SourceEnrichmentJob),
    ],
)
def test_persisted_handoff_survives_sessions_and_dispatches_only_to_solver(
    database, monkeypatch, kind, model
):
    monkeypatch.setattr(get_settings(), "solver_queue_enabled", True)
    with database.begin() as session:
        source = Source(
            name="Publisher",
            feed_url="https://publisher.example/feed",
            source_type="publisher",
            approval_status="approved",
        )
        session.add(source)
        session.flush()
        if model is SourceEnrichmentJob:
            owner = {"source_id": source.id}
        else:
            article = Article(
                canonical_url="https://publisher.example/article",
                url_hash=uuid.uuid4().hex,
                title="Article",
                metadata_source_type="publisher",
            )
            session.add(article)
            session.flush()
            owner = {"article_id": article.id}
        job = model(**owner, status="running", attempts=1)
        session.add(job)
        session.flush()
        job_id = job.id
        assert defer_to_solver(job, FeedError("challenge", reason="browser_challenge"), "Challenge")
    calls = []
    queue = SimpleNamespace(
        enqueue=lambda *a, **kw: calls.append((a, kw)) or SimpleNamespace(id="delivery")
    )
    assert dispatch_jobs(database, queue, 10, utcnow(), kind=kind) == 0
    if model is SourceEnrichmentJob:
        assert dispatch_jobs(database, queue, 10, utcnow(), kind=kind, source_analysis=True) == 0
    assert dispatch_jobs(database, queue, 10, utcnow(), kind=kind, solver=True) == 1
    assert calls[0][0][1] == str(job_id)
    assert calls[0][1]["job_timeout"] == 240
    with database() as session:
        job = session.get(model, job_id)
        assert job.requires_solver and job.attempts == 0 and job.dispatched_at


@pytest.mark.integration
def test_source_handler_persists_handoff_and_identifies_http_status(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "solver_queue_enabled", True)
    with database.begin() as session:
        source = Source(
            name="Publisher",
            feed_url="https://publisher.example/feed",
            source_type="publisher",
            approval_status="approved",
        )
        session.add(source)
        session.flush()
        job = SourceEnrichmentJob(source_id=source.id, available_at=utcnow() - timedelta(seconds=1))
        session.add(job)
        session.flush()
        identifier = job.id

    def lookup(*a):
        return {"description": "Feed metadata survives"}, FeedError(
            "private", reason="browser_challenge", status=403, resource="Source website"
        )

    monkeypatch.setattr(source_tasks, "lookup_profile", lookup)
    source_tasks.enrich_source(str(identifier))
    with database() as session:
        job = session.get(SourceEnrichmentJob, identifier)
        source = session.get(Source, job.source_id)
        assert job.requires_solver and job.status == "queued" and job.attempts == 0
        assert source.description == "Feed metadata survives"
        assert "Source website requires browser verification (HTTP 403)" in job.error
