import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from article_step_helpers import article_step_client
from devfeed_aggregator import analysis_tasks
from devfeed_aggregator.codex_client import AnalysisError
from devfeed_core import analysis
from devfeed_core.models import Article, ArticleAnalysisJob, utcnow
from sqlalchemy.exc import OperationalError

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
    monkeypatch.setattr(analysis_tasks, "snapshot_current", lambda *args: True)
    monkeypatch.setattr(analysis_tasks, "apply_publication_policy", lambda *args, **kwargs: {})
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
        ai_description="Consistent application deployments through infrastructure automation.",
        topics=[SUPPORTED_TOPIC],
        tags=[],
        reasons=[],
    )
    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=lambda *a: output)
    )
    return article, job, settings, statements


def test_worker_claims_waiting_lock_and_persists_analysis_without_publication(runtime):
    article, job, _, statements = runtime
    job.prompt_version = "outdated-test-prompt"
    analysis_tasks._analyze(job.id)
    assert job.status == "succeeded" and job.outcome == "applied"
    assert job.attempts == 1 and job.model == "configured-model"
    assert job.prompt_version == analysis.PROMPT_VERSION
    assert job.prompt_version == "article-analysis-v9-specialist-steps"
    assert "proposed_topics" not in job.result
    assert job.result["ai_summary"] == article.ai_summary
    assert len(job.catalog_snapshot["topics"]) == 1
    assert article.publication_status == "unpublished" and article.review_status == "pending"
    assert "FOR UPDATE" in str(statements[0]) and "SKIP LOCKED" not in str(statements[0])


def test_application_deadlock_retry_never_repeats_inference_or_holds_a_transaction(
    runtime, monkeypatch
):
    article, job, _, _ = runtime
    factory = analysis_tasks.session_factory()
    state = {"transactions": 0, "inferences": 0, "applies": 0}
    original_apply = analysis_tasks.apply_analysis
    client = analysis_tasks.CodexClient(None)
    original_complete = client.complete

    class TrackingFactory:
        @contextmanager
        def begin(self):
            state["transactions"] += 1
            try:
                with factory.begin() as session:
                    yield session
            finally:
                state["transactions"] -= 1

        __call__ = begin

    def complete(*args):
        assert state["transactions"] == 0
        state["inferences"] += 1
        return original_complete(*args)

    class Deadlock(Exception):
        sqlstate = "40P01"

    def apply(*args):
        assert state["transactions"] == 1
        state["applies"] += 1
        if state["applies"] == 1:
            raise OperationalError("test", {}, Deadlock())
        original_apply(*args)

    monkeypatch.setattr(analysis_tasks, "session_factory", TrackingFactory)
    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: client)
    monkeypatch.setattr(client, "complete", complete)
    monkeypatch.setattr(analysis_tasks, "apply_analysis", apply)
    analysis_tasks._analyze(job.id)
    assert state == {"transactions": 0, "inferences": 9, "applies": 2}
    assert job.outcome == "applied" and article.ai_summary == "Generated preview"
    assert list(job.usage["attempts"]) == ["1"]


@pytest.mark.parametrize("invalid_output", [False, True])
def test_lost_lease_prevents_both_result_application_and_failure_updates(
    runtime, monkeypatch, invalid_output
):
    article, job, _, _ = runtime
    telemetry = []
    original_complete = analysis_tasks.CodexClient(None).complete

    def complete(*args):
        job.lease_token = uuid.uuid4()  # Another process recovered this lease.
        if invalid_output:
            raise ValueError("invalid")
        return original_complete(*args)

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
    monkeypatch.setattr(analysis_tasks, "record_attempt", lambda *args, **kw: telemetry.append(kw))
    analysis_tasks._analyze(job.id)
    assert job.status == "running" and job.result is None and job.error is None
    assert article.ai_summary is None and article.review_status == "pending"
    assert telemetry == [{"attempt": 1}]


def test_worker_keeps_relevant_analysis_incomplete_without_a_primary_topic(runtime, monkeypatch):
    article, job, _, _ = runtime
    monkeypatch.setattr(
        analysis_tasks,
        "CodexClient",
        lambda _: article_step_client(
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
                "ai_description": "A guide to reliable infrastructure automation.",
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


@pytest.mark.parametrize("compact", [False, True], ids=["original", "passages"])
def test_insufficient_source_overview_never_overwrites_existing_prose(
    runtime, monkeypatch, compact
):
    article, job, settings, _ = runtime
    settings.ai_compact_article_prompts = compact
    article.ai_summary = "Existing reviewed overview."
    article.ai_description = "Existing description."
    article.publication_status = "unpublished"
    article.review_status = "approved"

    def complete(prompt, schema):
        assert "Do exactly the assigned task" in prompt
        return dict(
            outcome="insufficient_evidence",
            developer_relevance="uncertain",
            language=None,
            content_type=None,
            content_format=None,
            ai_summary="An unsupported expanded overview that must be discarded.",
            ai_description="Unsupported description.",
            ai_title=None,
            title_evidence=None,
            page_kind="uncertain",
            topics=[],
            tags=[],
            reasons=["The source only provides a teaser without supporting detail."],
        )

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
    analysis_tasks._analyze(job.id)

    assert job.status == "succeeded" and job.outcome == "insufficient_evidence"
    assert job.result["ai_summary"] is job.result["ai_description"] is None
    assert article.ai_summary == "Existing reviewed overview."
    assert article.ai_description == "Existing description."
    assert article.publication_status == "unpublished" and article.review_status == "approved"


@pytest.mark.parametrize("compact", [False, True], ids=["original", "passages"])
def test_worker_preserves_published_content_before_creating_an_inference_client(
    runtime, monkeypatch, compact
):
    article, job, settings, _ = runtime
    settings.ai_compact_article_prompts = compact
    article.ai_summary = "The existing published overview."
    article.ai_description = "The existing published description."
    article.publication_status = "published"
    article.review_status = "approved"
    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: pytest.fail("Started published article inference")
    )

    analysis_tasks._analyze(job.id)

    assert job.status == "succeeded" and job.outcome == "published_preserved"
    assert job.attempts == 0
    assert article.ai_summary == "The existing published overview."
    assert article.ai_description == "The existing published description."
    assert article.publication_status == "published" and article.review_status == "approved"


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
    article.publication_status = "unpublished"
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
        ai_description="A guide to deploying reliable applications.",
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
        return response

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
    analysis_tasks._analyze(job.id)
    assert job.status == "queued"
    failed = "overview" if field == "ai_summary" else "description"
    assert job.steps[failed]["validation_feedback"]["code"] == "non_english_ai_prose"
    assert article.ai_summary == "The original English summary is still available."
    assert article.publication_status == "unpublished"
    output[field] = "The article explains how developers deploy reliable applications."
    job.available_at = utcnow()
    analysis_tasks._analyze(job.id)
    assert job.status == "succeeded" and job.outcome == "applied"
    assert any("previous attempt at this task failed validation" in prompt for prompt in prompts)
    assert job.usage["prompt_format"] == "specialist-steps-v1"
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
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=lambda *a: output)
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

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
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

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
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

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
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

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
    analysis_tasks._analyze(job.id)
    assert job.status == "running" and job.lease_token == new_token
    assert job.attempts == 2 and job.error is None


def test_specialists_keep_verbatim_evidence_with_legacy_compact_setting(runtime, monkeypatch):

    article, job, settings, _ = runtime
    settings.ai_compact_article_prompts = True
    topic = {"id": str(uuid.uuid4()), "name": "Infrastructure", "slug": "infrastructure"}
    monkeypatch.setattr(analysis_tasks, "catalog", lambda _: {"topics": [topic], "tags": []})

    def complete(prompt, schema):
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
            ai_description="The article explains how developers deploy reliable applications.",
            tags=[],
            reasons=[],
            topics=[
                dict(topic_id=topic["id"], role="primary", relevance=1, evidence=article.title)
            ],
        )

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
    analysis_tasks._analyze(job.id)
    assert job.status == "succeeded"
    assert job.result["topics"][0]["evidence"] == article.title
    assert job.usage["prompt_format"] == "specialist-steps-v1"


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

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
    monkeypatch.setattr(analysis_tasks, "apply_analysis", apply)
    monkeypatch.setattr("devfeed_core.transaction_retry.time.sleep", lambda _: None)
    analysis_tasks._analyze(job.id)
    assert job.status == "succeeded" and job.attempts == 1
    assert len(inference) == 9 and len(applications) == 2
    assert article.ai_summary


def test_identical_invalid_output_stops_after_corrective_attempt(runtime, monkeypatch):
    _, job, settings, _ = runtime
    settings.ai_tiered_routing_enabled = False

    def complete(*args):
        return {"not": "the required schema"}

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=complete)
    )
    analysis_tasks._analyze(job.id)
    assert job.status == "queued"
    assert len(job.steps["developer_relevance"]["invalid_output_hash"]) == 64
    job.available_at = utcnow()
    analysis_tasks._analyze(job.id)
    assert job.status == "failed" and job.attempts == 2
    assert job.error == "invalid_analysis_result"


def test_specialists_receive_one_task_and_apply_the_complete_bundle_atomically(
    runtime, monkeypatch
):
    article, job, _, _ = runtime
    complete = analysis_tasks.CodexClient(None).complete
    fields, descriptions = [], []

    def infer(prompt, schema):
        assert article.ai_summary is None and article.ai_description is None
        own = set(schema["properties"]) - {"outcome", "reason"}
        fields.append(own)
        if "ai_description" in own:
            descriptions.append(json.loads(prompt.rsplit("\n", 1)[1]))
        assert job.lease_until > utcnow()
        assert job.active_step is not None
        return complete(prompt, schema)

    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=infer))
    analysis_tasks._analyze(job.id)
    assert fields == [
        {"developer_relevance"},
        {"page_kind"},
        {"language"},
        {"content_type"},
        {"content_format"},
        {"topics"},
        {"tags"},
        {"ai_summary"},
        {"ai_description"},
    ]
    assert set(descriptions[0]) == {"source_title", "overview"}
    assert descriptions[0]["overview"] == article.ai_summary
    assert job.active_step is None and job.outcome == "applied"
    assert all(step["status"] in {"succeeded", "skipped"} for step in job.steps.values())
    assert job.steps["identifying_title"]["status"] == "skipped"


def test_failed_description_reuses_successful_steps_and_counts_every_call(runtime, monkeypatch):
    article, job, _, _ = runtime
    complete = analysis_tasks.CodexClient(None).complete
    calls, failed = [], False
    client = SimpleNamespace(usage={}, model="resolved-escalation-model")

    def infer(prompt, schema):
        nonlocal failed
        own = sorted(set(schema["properties"]) - {"outcome", "reason"})
        calls.append(own)
        client.usage = {"input_tokens": 2, "output_tokens": 1}
        if "ai_description" in own and not failed:
            failed = True
            raise AnalysisError("codex_timeout")
        return complete(prompt, schema)

    client.complete = infer
    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: client)
    analysis_tasks._analyze(job.id)
    assert job.status == "queued" and job.active_step == "description"
    assert article.ai_summary is None
    assert len([step for step in job.steps.values() if step["status"] == "succeeded"]) == 8
    assert job.steps["description"]["attempts"] == 1
    assert job.steps["overview"]["resolved_model"] == "resolved-escalation-model"
    job.available_at = utcnow()
    analysis_tasks._analyze(job.id)
    assert job.outcome == "applied" and len(calls) == 10
    assert calls[-2:] == [["ai_description"], ["ai_description"]]
    assert job.usage["input_tokens"] == 20 and job.usage["output_tokens"] == 10
    assert job.steps["description"]["attempts"] == 2


@pytest.mark.parametrize("change", ["model", "routing", "prompt", "topic_prompt"])
def test_changed_specialist_identity_invalidates_its_result_and_downstream(
    runtime, monkeypatch, change
):
    from dataclasses import replace

    _, job, settings, _ = runtime
    complete = analysis_tasks.CodexClient(None).complete
    calls, failed = [], False

    def infer(prompt, schema):
        nonlocal failed
        own = set(schema["properties"]) - {"outcome", "reason"}
        calls.append(own)
        if "ai_description" in own and not failed:
            failed = True
            raise AnalysisError("codex_timeout")
        return complete(prompt, schema)

    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=infer))
    analysis_tasks._analyze(job.id)
    assert job.status == "queued"
    if change == "model":
        settings.codex_model = "another-model"
    elif change == "routing":
        settings.ai_fast_model = "different-fast-model"
    else:
        step = "topics" if change == "topic_prompt" else "overview"
        spec = analysis_tasks.STEP_SPECS[step]
        monkeypatch.setitem(
            analysis_tasks.STEP_SPECS, step, replace(spec, prompt_version=f"{step}-v2-test")
        )
    job.available_at = utcnow()
    analysis_tasks._analyze(job.id)
    assert job.outcome == "applied"
    assert len(calls) == (11 if change in {"prompt", "topic_prompt"} else 18)
    if change == "topic_prompt":
        assert sum("ai_summary" in call for call in calls) == 1


def test_each_specialist_has_its_own_retry_budget(runtime, monkeypatch):
    _, job, _, _ = runtime
    job.attempts = 20  # Earlier specialists may each have consumed retries.
    monkeypatch.setattr(
        analysis_tasks,
        "CodexClient",
        lambda _: SimpleNamespace(
            complete=lambda *_: (_ for _ in ()).throw(AnalysisError("codex_timeout"))
        ),
    )
    analysis_tasks._analyze(job.id)
    assert job.status == "queued" and job.attempts == 21
    assert job.steps["developer_relevance"]["attempts"] == 1
