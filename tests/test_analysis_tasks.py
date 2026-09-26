import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from devfeed_aggregator import analysis_tasks
from devfeed_aggregator.codex_client import AnalysisError
from devfeed_core import analysis
from devfeed_core.models import Article, ArticleAnalysisJob, utcnow

TOPIC_ID = uuid.uuid4()
SUPPORTED_TOPIC = {
    "topic_id": str(TOPIC_ID),
    "role": "primary",
    "relevance": 0.9,
    "evidence": "Infrastructure automation",
}


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
        scalars=lambda statement: [],
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
    taxonomy = {
        "topics": [{"id": str(TOPIC_ID), "name": "Infrastructure", "slug": "infrastructure"}],
        "tags": [],
    }
    monkeypatch.setattr(analysis_tasks, "catalog", lambda _: taxonomy)
    output = dict(
        outcome="ready",
        developer_relevance="relevant",
        language="en",
        content_type="article",
        content_format="article",
        ai_summary="Generated preview",
        ai_title=None,
        title_evidence=None,
        page_kind="article",
        ai_description=None,
        topics=[SUPPORTED_TOPIC],
        tags=[],
        reasons=[],
    )
    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=lambda *a: output)
    )
    return article, job, settings, statements


def test_worker_claims_waiting_lock_and_persists_analysis_without_publication(runtime):
    article, job, _, statements = runtime
    job.prompt_version = "outdated-test-prompt"
    analysis_tasks._analyze(job.id)
    assert job.status == "succeeded" and job.outcome == "applied"
    assert job.attempts == 1 and job.model == "configured-model"
    assert job.prompt_version == analysis.PROMPT_VERSION == "article-analysis-v5-page-purpose"
    assert "proposed_topics" not in job.result
    assert job.result["ai_summary"] == article.ai_summary
    assert len(job.catalog_snapshot["topics"]) == 1
    assert article.publication_status == "unpublished" and article.review_status == "pending"
    assert "FOR UPDATE" in str(statements[0]) and "SKIP LOCKED" not in str(statements[0])


def test_worker_keeps_relevant_analysis_incomplete_without_a_primary_topic(runtime, monkeypatch):
    article, job, _, _ = runtime
    monkeypatch.setattr(
        analysis_tasks,
        "CodexClient",
        lambda _: SimpleNamespace(
            complete=lambda *a: {
                "outcome": "ready",
                "developer_relevance": "relevant",
                "language": "en",
                "content_type": "article",
                "content_format": "article",
                "ai_summary": "Generated preview",
                "ai_title": None,
                "title_evidence": None,
                "page_kind": "article",
                "ai_description": None,
                "topics": [],
                "tags": [],
                "reasons": [],
            }
        ),
    )
    analysis_tasks._analyze(job.id)
    assert job.status == "succeeded" and job.outcome == "insufficient_evidence"
    assert job.result["topic_match_status"] == "no_topic_match"
    assert job.result["ai_summary"] is None
    assert article.ai_summary is None
    assert article.publication_status == "unpublished" and article.review_status == "pending"


def test_worker_refuses_stale_editorial_revision_before_spending_inference(runtime, monkeypatch):
    article, job, _, _ = runtime
    article.editorial_revision += 1
    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: pytest.fail("Started inference"))
    analysis_tasks._analyze(job.id)
    assert job.outcome == "superseded" and job.attempts == 0


@pytest.mark.parametrize("field", ["ai_summary", "ai_description"])
@pytest.mark.parametrize("compact", [False, True])
def test_worker_retries_non_english_prose_without_overwriting_article(
    runtime, monkeypatch, field, compact
):
    article, job, settings, _ = runtime
    settings.ai_compact_article_prompts = compact
    article.ai_summary = "The original English summary is still available."
    article.publication_status = "published"
    article.review_status = "approved"
    prompts = []
    output = dict(
        outcome="ready",
        developer_relevance="relevant",
        language="en",
        content_type="article",
        content_format="article",
        ai_summary="The article explains how developers deploy reliable applications.",
        ai_title=None,
        title_evidence=None,
        page_kind="article",
        ai_description=None,
        topics=[SUPPORTED_TOPIC],
        tags=[],
        reasons=[],
    )
    output[field] = (
        "Este artículo explica cómo crear aplicaciones fiables y guardar la información "
        "en una base de datos. Incluye ejemplos prácticos para los desarrolladores."
    )

    def complete(prompt, schema):
        prompts.append(prompt)
        response = output.copy()
        if compact:
            initial_prompt = prompt.split("\nThe previous attempt failed validation.", 1)[0]
            article_input = json.loads(initial_prompt.rsplit("\n", 1)[1])["article"]
            response["topics"] = [{**SUPPORTED_TOPIC, "evidence": article_input["title"][0]["id"]}]
        return response

    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    analysis_tasks._analyze(job.id)
    assert job.status == "queued"
    assert job.result["validation_feedback"]["code"] == "non_english_ai_prose"
    assert article.ai_summary == "The original English summary is still available."
    assert article.publication_status == "published"
    output[field] = "The article explains how developers deploy reliable applications."
    job.available_at = utcnow()
    analysis_tasks._analyze(job.id)
    assert job.status == "succeeded" and job.outcome == "applied"
    assert "Rewrite both ai_summary and ai_description in clear English" in prompts[-1]
    assert "always in English regardless" in prompts[-1]
    assert article.language == "en"


def test_worker_preserves_foreign_source_language_with_english_prose(runtime, monkeypatch):
    article, job, _, _ = runtime
    output = dict(
        outcome="ready",
        developer_relevance="relevant",
        language="ja",
        content_type="article",
        content_format="article",
        ai_summary="This article explains how to deploy reliable applications using Kubernetes.",
        ai_title=None,
        title_evidence=None,
        page_kind="article",
        ai_description="The tutorial covers deployment configuration and testing changes.",
        topics=[SUPPORTED_TOPIC],
        tags=[],
        reasons=[],
    )
    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=lambda *a: output)
    )
    analysis_tasks._analyze(job.id)
    assert job.outcome == "applied"
    assert article.language == "ja"
    assert article.ai_summary == output["ai_summary"]


def test_provider_overload_during_corrective_retry_keeps_job_queued(runtime, monkeypatch):
    article, job, settings, _ = runtime
    settings.ai_tiered_routing_enabled = True
    job.attempts = 1
    job.result = {"validation_feedback": {"code": "non_english_ai_prose", "fields": []}}

    def complete(*args):
        raise AnalysisError("codex_server_overloaded")

    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    monkeypatch.setattr(analysis_tasks, "safe_pause", lambda _, **kwargs: 600)
    analysis_tasks._analyze(job.id)
    assert job.status == "queued" and job.error == "codex_server_overloaded"
    assert job.usage["capacity_deferrals"] == 1
    assert job.available_at > utcnow()
    assert article.ai_summary is None


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
            ai_title=None,
            title_evidence=None,
            page_kind="article",
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


def test_worker_restores_passage_evidence_before_applying_analysis(runtime, monkeypatch):
    import json

    article, job, settings, _ = runtime
    settings.ai_compact_article_prompts = True
    topic = {"id": str(uuid.uuid4()), "name": "Infrastructure", "slug": "infrastructure"}
    monkeypatch.setattr(analysis_tasks, "catalog", lambda _: {"topics": [topic], "tags": []})

    def complete(prompt, schema):
        data = json.loads(prompt.rsplit("\n", 1)[1])
        passage = data["article"]["title"][0]
        return dict(
            outcome="ready",
            developer_relevance="relevant",
            language="en",
            content_type="article",
            content_format="article",
            ai_summary="Generated preview",
            ai_title=None,
            title_evidence=None,
            page_kind="article",
            ai_description=None,
            tags=[],
            reasons=[],
            topics=[
                dict(topic_id=topic["id"], role="primary", relevance=1, evidence=passage["id"])
            ],
        )

    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    analysis_tasks._analyze(job.id)
    assert job.status == "succeeded"
    assert job.result["topics"][0]["evidence"] == article.title
    assert job.usage["prompt_format"] == "compact-evidence-v3"


def test_deadlock_retries_apply_without_repeating_inference(runtime, monkeypatch):
    from sqlalchemy.exc import OperationalError

    article, job, _, _ = runtime
    inference, applications = [], []
    original = analysis_tasks.apply_analysis
    client = analysis_tasks.CodexClient(None)

    def complete(*args):
        inference.append(True)
        return client.complete(*args)

    class Deadlock(Exception):
        sqlstate = "40P01"

    def apply(*args):
        applications.append(True)
        if len(applications) == 1:
            raise OperationalError("redacted", {}, Deadlock())
        return original(*args)

    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    monkeypatch.setattr(analysis_tasks, "apply_analysis", apply)
    monkeypatch.setattr("devfeed_core.transaction_retry.time.sleep", lambda _: None)
    analysis_tasks._analyze(job.id)
    assert job.status == "succeeded" and job.attempts == 1
    assert len(inference) == 1 and len(applications) == 2
    assert article.ai_summary


def test_identical_invalid_output_stops_after_corrective_attempt(runtime, monkeypatch):
    _, job, settings, _ = runtime
    settings.ai_tiered_routing_enabled = False

    def complete(*args):
        return {"not": "the required schema"}

    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    analysis_tasks._analyze(job.id)
    assert job.status == "queued"
    assert len(job.usage["invalid_output_hash"]) == 64
    job.available_at = utcnow()
    analysis_tasks._analyze(job.id)
    assert job.status == "failed" and job.attempts == 2
    assert job.error == "invalid_analysis_result"
