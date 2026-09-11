import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from devfeed_aggregator import analysis_tasks
from devfeed_aggregator.codex_client import AnalysisError
from devfeed_core import analysis
from devfeed_core.models import Article, ArticleAnalysisJob, utcnow


@pytest.fixture
def runtime(monkeypatch):
    article = Article(
        id=uuid.uuid4(),
        canonical_url="https://example.com/ai",
        title="Infrastructure automation",
        summary=("Infrastructure automation helps developers deploy applications consistently."),
        editorial_revision=0,
        review_status="pending",
        publication_status="unpublished",
        metadata_source_type="publisher",
    )
    snapshot = analysis.source_snapshot(article, None)
    job = ArticleAnalysisJob(
        id=uuid.uuid4(),
        article_id=article.id,
        status="queued",
        attempts=0,
        available_at=utcnow(),
        input_snapshot=snapshot,
        input_hash=analysis.snapshot_hash(snapshot),
        editorial_revision=0,
        prompt_version=analysis.PROMPT_VERSION,
    )
    statements = []

    def scalar(statement):
        statements.append(statement)
        return job if statement.column_descriptions[0]["entity"] is ArticleAnalysisJob else article

    session = SimpleNamespace(
        scalar=scalar,
        get=lambda *_: None,
        flush=lambda: None,
        info={},
        execute=lambda *_: None,
        add=lambda *_: None,
        expire=lambda *a: None,
    )

    class Factory:
        @contextmanager
        def begin(self):
            yield session

        __call__ = begin

    settings = SimpleNamespace(
        ai_enabled=True, full_automation=False, codex_model="configured-model"
    )
    monkeypatch.setattr(analysis_tasks, "session_factory", lambda: Factory())
    monkeypatch.setattr(analysis_tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(analysis_tasks, "approved_sources", lambda *a, **kw: [uuid.uuid4()])
    monkeypatch.setattr(analysis, "approved_sources", lambda *a, **kw: [uuid.uuid4()])
    taxonomy = {"topics": [], "tags": []}
    monkeypatch.setattr(analysis_tasks, "catalog", lambda _: taxonomy)
    output = dict(
        outcome="ready",
        developer_relevance="relevant",
        language="en",
        content_type="article",
        content_format="article",
        ai_summary="Generated preview",
        ai_description=None,
        topics=[],
        tags=[],
        reasons=[],
    )
    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=lambda *a: output)
    )
    return article, job, settings, statements


def test_worker_claims_waiting_lock_and_persists_analysis_without_publication(runtime):
    article, job, _, statements = runtime
    job.prompt_version = "article-analysis-v3"
    analysis_tasks._analyze(job.id)
    assert job.status == "succeeded" and job.outcome == "applied"
    assert job.attempts == 1 and job.model == "configured-model"
    assert job.prompt_version == analysis.PROMPT_VERSION == "article-analysis-v4"
    assert "proposed_topics" not in job.result
    assert job.result["ai_summary"] == article.ai_summary
    assert job.catalog_snapshot == {"topics": [], "tags": []}
    assert article.publication_status == "unpublished" and article.review_status == "pending"
    assert "FOR UPDATE" in str(statements[0]) and "SKIP LOCKED" not in str(statements[0])


def test_worker_refuses_stale_editorial_revision_before_spending_inference(runtime, monkeypatch):
    article, job, _, _ = runtime
    article.editorial_revision += 1
    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: pytest.fail("Started inference"))
    analysis_tasks._analyze(job.id)
    assert job.outcome == "superseded" and job.attempts == 0


def test_worker_lease_loss_discards_result(runtime, monkeypatch):
    article, job, _, _ = runtime

    def complete(*args):
        job.lease_token = uuid.uuid4()
        return dict(
            outcome="insufficient_evidence",
            developer_relevance="uncertain",
            language=None,
            content_type=None,
            content_format=None,
            ai_summary=None,
            ai_description=None,
            topics=[],
            tags=[],
            reasons=[],
        )

    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    analysis_tasks._analyze(job.id)
    assert job.status == "running" and article.ai_summary is None


@pytest.mark.parametrize(
    "reason,status",
    [
        ("codex_timeout", "queued"),
        ("unexpected_server_request", "failed"),
        ("unexpected_tool_execution", "failed"),
        ("ai_not_configured", "failed"),
    ],
)
def test_worker_retries_transient_errors_only(runtime, monkeypatch, reason, status):
    article, job, _, _ = runtime

    def complete(*args):
        raise AnalysisError(reason)

    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    analysis_tasks._analyze(job.id)
    assert job.status == status and job.error == reason
    assert job.lease_token is None and job.dispatched_at is None
    assert article.ai_summary is None and article.publication_status == "unpublished"


def test_failure_after_lease_loss_cannot_requeue_new_owner(runtime, monkeypatch):
    _, job, _, _ = runtime
    new_token = uuid.uuid4()

    def complete(*args):
        job.lease_token = new_token
        job.attempts += 1
        raise AnalysisError("codex_timeout")

    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    analysis_tasks._analyze(job.id)
    assert job.status == "running" and job.lease_token == new_token
    assert job.attempts == 2 and job.error is None
