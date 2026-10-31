import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from devfeed_aggregator import analysis_tasks
from devfeed_aggregator.codex_client import AnalysisError
from devfeed_core import analysis
from devfeed_core.models import Article, ArticleAnalysisJob, utcnow
from sqlalchemy.exc import OperationalError

from tests.support.article_step_helpers import article_step_client

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
        ai_enabled=True,
        full_automation=False,
        codex_model="configured-model",
        analysis_catalog_reuse_enabled=False,
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
    claim = statements[1]
    assert "FOR UPDATE" not in str(statements[0])  # Metadata identifies a private owner first.
    assert "FOR UPDATE" in str(claim) and "SKIP LOCKED" not in str(claim)


@pytest.mark.parametrize("retained", [False, True])
def test_application_deadlock_retry_never_repeats_inference_or_holds_a_transaction(
    runtime, monkeypatch, retained
):
    article, job, settings, _ = runtime
    settings.analysis_catalog_reuse_enabled = retained
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
    analysis_tasks.analyze_article(str(job.id))
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


@pytest.mark.parametrize("blocked", ["disabled", "unapproved", "deleted"])
def test_claim_rejects_unavailable_inputs_before_creating_a_paid_client(
    runtime, monkeypatch, blocked
):
    from unittest.mock import Mock

    article, job, settings, _ = runtime
    if blocked == "disabled":
        settings.ai_enabled = False
    elif blocked == "unapproved":
        monkeypatch.setattr(analysis_tasks, "approved_sources", lambda *_args, **_kwargs: [])
    else:
        factory = analysis_tasks.session_factory()
        with factory() as session:
            scalar = session.scalar
            session.scalar = lambda statement: (
                scalar(statement)
                if statement.column_descriptions[0]["entity"] is ArticleAnalysisJob
                else None
            )
    client = Mock(
        side_effect=AssertionError("No external inference for unavailable business inputs")
    )
    monkeypatch.setattr(analysis_tasks, "CodexClient", client)
    analysis_tasks._analyze(job.id)
    assert (
        job.outcome
        == {"disabled": None, "unapproved": "unapproved", "deleted": "superseded"}[blocked]
    )
    assert job.status == ("failed" if blocked == "disabled" else "succeeded")
    assert article.ai_summary is None
    client.assert_not_called()


def test_application_warms_changed_source_snapshot_after_closing_its_read_scope(
    runtime, monkeypatch
):
    from unittest.mock import Mock

    article, job, _, _ = runtime
    previous = dict(job.input_snapshot)
    article.summary += " Updated after the inference request."
    candidates = Mock(return_value={})
    monkeypatch.setattr(analysis_tasks, "analysis_candidates", candidates)
    _, current = analysis_tasks._prepare_application(
        analysis_tasks.session_factory(), article.id, previous
    )
    assert current != previous
    assert [call.args[1] for call in candidates.call_args_list] == [previous, current]


def analysis_service(runtime):
    article, job, settings, _ = runtime
    service = analysis_tasks.ArticleAnalysisService(
        settings, analysis_tasks.session_factory(), analysis_tasks.CodexClient
    )
    context = analysis_tasks.AnalysisContext(job.id, uuid.uuid4(), job.input_snapshot, article.id)
    return service, context


@pytest.mark.parametrize("phase", ["prepare", "infer", "start", "propose", "apply"])
def test_every_analysis_phase_stops_when_its_lease_is_lost(runtime, monkeypatch, phase):
    from dataclasses import replace
    from unittest.mock import Mock

    _, job, settings, _ = runtime
    settings.full_automation = True
    service, context = analysis_service(runtime)
    monkeypatch.setattr(analysis_tasks, "owned_job", lambda *_args: None)
    result = SimpleNamespace(developer_relevance="relevant", page_kind="article")
    attempt = analysis_tasks.AnalysisAttempt()
    client = Mock(side_effect=AssertionError("No call after lease loss"))
    service = replace(service, client_factory=client)
    if phase == "prepare":
        service.run(context)
    elif phase == "infer":
        with pytest.raises(analysis_tasks._AnalysisStopped):
            service.infer(
                context, analysis_tasks.PreparedAnalysis({}, None, False, "test"), attempt
            )
    elif phase == "start":
        with pytest.raises(analysis_tasks._AnalysisStopped):
            service.start_step(context, "overview", "digest")
    elif phase == "propose":
        service.propose_topics(context, result)
    else:
        assert service.apply(None, context, result, None, attempt, {}, None) is None
    assert job.status == "queued" and job.result is None
    client.assert_not_called()


@pytest.mark.parametrize("blocked", ["unapproved", "deleted", "deferred", "published"])
def test_application_rechecks_business_visibility_and_never_overwrites_withdrawn_inputs(
    runtime, monkeypatch, blocked
):
    article, job, _, _ = runtime
    service, context = analysis_service(runtime)
    job.status = "running"
    monkeypatch.setattr(analysis_tasks, "owned_job", lambda *_args: job)
    if blocked == "unapproved":
        monkeypatch.setattr(analysis_tasks, "approved_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        analysis_tasks.ArticleAnalysisService,
        "lock_article",
        staticmethod(lambda *_args: None if blocked == "deleted" else article),
    )
    monkeypatch.setattr(analysis_tasks, "eligible_article", lambda _: blocked != "deferred")
    if blocked == "published":
        article.publication_status = "published"
    result = SimpleNamespace(model_dump=lambda **_kwargs: {"validated": True})
    with service.factory() as session:
        outcome = service.apply(
            session, context, result, None, analysis_tasks.AnalysisAttempt(), {}, None
        )
    assert (
        outcome
        == {
            "unapproved": "unapproved",
            "deleted": "superseded",
            "deferred": "content_date_deferred",
            "published": "published_preserved",
        }[blocked]
    )
    assert job.status == "succeeded" and article.ai_summary is None


@pytest.mark.parametrize("changed", ["source", "catalog", "saved_taxonomy"])
def test_apply_prepared_refuses_source_and_catalog_drift_without_partial_publication(
    runtime, monkeypatch, changed
):
    from unittest.mock import Mock

    article, job, _, _ = runtime
    service, context = analysis_service(runtime)
    apply = Mock()
    refresh = Mock()
    monkeypatch.setattr(analysis_tasks, "apply_analysis", apply)
    monkeypatch.setattr(analysis_tasks, "refresh_superseded_analysis", refresh)
    monkeypatch.setattr(analysis_tasks, "lock_topics", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(analysis_tasks, "source_snapshot", lambda *_args: {"current": True})
    monkeypatch.setattr(analysis_tasks, "snapshot_current", lambda *_args: changed != "catalog")
    monkeypatch.setattr(analysis_tasks, "analysis_catalog_current", lambda *_args: False)
    with service.factory() as session:
        if changed == "saved_taxonomy":
            service.apply_current(session, context, object(), article, job, {}, {"current": True})
            assert job.outcome == "superseded"
            refresh.assert_called_once()
        else:
            with pytest.raises(analysis_tasks._ApplicationInputsChanged):
                service.apply_current(
                    session,
                    context,
                    object(),
                    article,
                    job,
                    {},
                    {} if changed == "source" else {"current": True},
                )
            refresh.assert_not_called()
    apply.assert_not_called()


def test_dependency_failure_before_any_specialist_uses_durable_retry_without_fake_usage(
    runtime, monkeypatch
):
    from unittest.mock import Mock

    _, job, _, _ = runtime
    service, context = analysis_service(runtime)
    job.status, job.attempts = "running", 1
    monkeypatch.setattr(analysis_tasks, "owned_job", lambda *_args: job)
    monkeypatch.setattr(
        analysis_tasks.ArticleAnalysisService,
        "prepare",
        lambda *_args: analysis_tasks.PreparedAnalysis({}, None, False, "test"),
    )
    monkeypatch.setattr(
        analysis_tasks.ArticleAnalysisService,
        "infer",
        Mock(side_effect=RuntimeError("dependency lost")),
    )
    recorded = Mock()
    monkeypatch.setattr(analysis_tasks, "record_attempt", recorded)
    service.run(context)
    assert job.status == "queued" and job.error == "analysis_dependency_failure"
    assert job.active_step is None
    recorded.assert_not_called()


def test_invalid_saved_specialist_output_is_revalidated_and_replaced_before_publication(
    runtime, monkeypatch
):
    _, job, _, _ = runtime
    complete = analysis_tasks.CodexClient(None).complete
    calls, failed = [], False

    def infer(prompt, schema):
        nonlocal failed
        fields = set(schema["properties"]) - {"outcome", "reason"}
        calls.append(fields)
        if "ai_description" in fields and not failed:
            failed = True
            raise AnalysisError("codex_timeout")
        return complete(prompt, schema)

    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=infer)
    )
    analysis_tasks._analyze(job.id)
    assert job.status == "queued"
    steps = dict(job.steps)
    steps["developer_relevance"] = steps["developer_relevance"] | {
        "output": {"invalid": "saved output"}
    }
    job.steps = steps
    job.available_at = utcnow()
    analysis_tasks._analyze(job.id)
    assert job.outcome == "applied" and job.status == "succeeded"
    assert sum("developer_relevance" in fields for fields in calls) == 2
    assert job.steps["developer_relevance"]["output"] != {"invalid": "saved output"}


@pytest.mark.parametrize("enabled", [False, True])
def test_job_catalog_reuse_spans_specialists_and_apply_without_retaining_transactions(
    runtime, monkeypatch, enabled
):
    import weakref

    from devfeed_core import catalog_cache

    article, job, settings, _ = runtime
    settings.analysis_catalog_reuse_enabled = enabled
    factory = analysis_tasks.session_factory()
    taxonomy = analysis_tasks.catalog(None)
    state = {"transactions": 0, "inferences": 0, "revision_reads": 0, "loads": 0}
    references = []
    original = analysis._PreparedCatalog
    complete = analysis_tasks.CodexClient(None).complete

    class TrackedCatalog(original):
        def __init__(self, taxonomy):
            super().__init__(taxonomy)
            references.append(weakref.ref(self))

    class TrackingFactory:
        @contextmanager
        def begin(self):
            state["transactions"] += 1
            try:
                with factory.begin() as session:
                    session.get_bind = lambda: factory
                    yield session
            finally:
                state["transactions"] -= 1

        __call__ = begin

    def revisions(*_):
        state["revision_reads"] += 1
        return (("tags", "revision"), ("topics", "revision"))

    def load(_):
        state["loads"] += 1
        return json.loads(json.dumps(taxonomy))

    def infer(*args):
        assert state["transactions"] == 0
        state["inferences"] += 1
        assert (analysis._ranking.get() is not None) == enabled
        return complete(*args)

    monkeypatch.setattr(analysis_tasks, "session_factory", TrackingFactory)
    monkeypatch.setattr(analysis_tasks, "catalog", analysis.catalog)
    monkeypatch.setattr(analysis_tasks, "snapshot_current", catalog_cache.snapshot_current)
    monkeypatch.setattr(analysis, "_read_catalog", load)
    monkeypatch.setattr(catalog_cache, "_revisions", revisions)
    monkeypatch.setattr(analysis, "_PreparedCatalog", TrackedCatalog)
    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=infer)
    )
    analysis_tasks.analyze_article(str(job.id))
    assert job.outcome == "applied" and article.ai_summary == "Generated preview"
    assert state["transactions"] == 0 and state["inferences"] == 9
    assert len(references) == (1 if enabled else 2)
    assert state["loads"] == (1 if enabled else 2)
    assert state["revision_reads"] == (4 if enabled else 3)
    assert all(reference() is None for reference in references)
    assert analysis._ranking.get() is None and catalog_cache._snapshots.get() is None


@pytest.mark.parametrize("stopped", ["lease", "dependency", "invalid", "exception"])
def test_job_catalog_reuse_cleans_up_after_interrupted_inference(runtime, monkeypatch, stopped):
    import weakref

    from devfeed_core import catalog_cache

    _, job, settings, _ = runtime
    settings.analysis_catalog_reuse_enabled = True
    references, containers = [], []
    original = analysis._PreparedCatalog
    complete = analysis_tasks.CodexClient(None).complete

    class TrackedCatalog(original):
        def __init__(self, taxonomy):
            super().__init__(taxonomy)
            references.append(weakref.ref(self))

    def infer(*args):
        containers[:] = [analysis._ranking.get(), catalog_cache._snapshots.get()]
        if stopped == "lease":
            job.lease_token = uuid.uuid4()
            return complete(*args)
        if stopped == "dependency":
            raise AnalysisError("codex_timeout")
        if stopped == "invalid":
            raise ValueError("malformed response")
        raise RuntimeError("transport unavailable")

    monkeypatch.setattr(analysis, "_PreparedCatalog", TrackedCatalog)
    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=infer)
    )
    analysis_tasks.analyze_article(str(job.id))
    assert job.outcome != "applied"
    assert len(references) == 1 and references[0]() is None
    assert containers == [[], {}]
    assert analysis._ranking.get() is None and catalog_cache._snapshots.get() is None


def test_durable_analysis_retry_starts_a_fresh_catalog_scope_and_reuses_saved_specialists(
    runtime, monkeypatch
):
    import weakref

    _, job, settings, _ = runtime
    settings.analysis_catalog_reuse_enabled = True
    original = analysis._PreparedCatalog
    complete = analysis_tasks.CodexClient(None).complete
    references, fields = [], []
    failed = False

    class TrackedCatalog(original):
        def __init__(self, taxonomy):
            assert not references or references[-1]() is None
            super().__init__(taxonomy)
            references.append(weakref.ref(self))

    def infer(prompt, schema):
        nonlocal failed
        names = set(schema["properties"])
        fields.append(names)
        if "ai_description" in names and not failed:
            failed = True
            raise AnalysisError("codex_timeout")
        return complete(prompt, schema)

    monkeypatch.setattr(analysis, "_PreparedCatalog", TrackedCatalog)
    monkeypatch.setattr(
        analysis_tasks, "CodexClient", lambda _: article_step_client(complete=infer)
    )
    analysis_tasks.analyze_article(str(job.id))
    assert job.status == "queued" and references[0]() is None
    job.available_at = utcnow()
    analysis_tasks.analyze_article(str(job.id))
    assert job.outcome == "applied" and job.attempts == 2
    assert sum("developer_relevance" in names for names in fields) == 1
    assert len(references) == 2 and all(reference() is None for reference in references)


def test_uncaught_runtime_failure_releases_owned_catalog_even_with_retained_traceback(
    runtime, monkeypatch
):
    import weakref

    from devfeed_core import catalog_cache

    _, job, settings, _ = runtime
    settings.analysis_catalog_reuse_enabled = True
    references, containers = [], []
    taxonomy = analysis_tasks.catalog(None)

    def fail(_):
        analysis.analysis_candidates(taxonomy, job.input_snapshot)
        references.append(weakref.ref(analysis._ranking.get()[0]))
        containers[:] = [analysis._ranking.get(), catalog_cache._snapshots.get()]
        raise RuntimeError("uncaught runtime failure")

    monkeypatch.setattr(analysis_tasks, "_analyze", fail)
    with pytest.raises(RuntimeError, match="uncaught runtime failure") as failure:
        analysis_tasks.analyze_article(str(job.id))
    assert failure.traceback is not None
    assert references[0]() is None and containers == [[], {}]


def test_concurrent_analysis_jobs_keep_catalog_scopes_independent(runtime, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from devfeed_core import catalog_cache

    _, _, settings, _ = runtime
    settings.analysis_catalog_reuse_enabled = True
    barrier = Barrier(2, timeout=10)
    scopes = []

    def run(identifier):
        taxonomy = {
            "topics": [{"id": str(identifier), "name": str(identifier), "slug": "job"}],
            "tags": [],
        }
        analysis.analysis_candidates(taxonomy, {"title": "job"})
        ranking, snapshots = analysis._ranking.get(), catalog_cache._snapshots.get()
        scopes.append((ranking, snapshots))
        barrier.wait()
        assert ranking[0].taxonomy is taxonomy
        assert catalog_cache._snapshots.get() is snapshots

    monkeypatch.setattr(analysis_tasks, "_analyze", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(analysis_tasks.analyze_article, [str(uuid.uuid4()), str(uuid.uuid4())]))
    assert scopes[0][0] is not scopes[1][0] and scopes[0][1] is not scopes[1][1]
    assert scopes == [([], {}), ([], {})]
    assert analysis._ranking.get() is None and catalog_cache._snapshots.get() is None
