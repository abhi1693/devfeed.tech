"""Explicit retry command policy over scoped reads and domain enqueue boundaries."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import (
    analysis,
    article_lifecycle,
    editorial,
    image_jobs,
    job_retries,
)
from devfeed_core import (
    article_processing_retry as retries,
)
from devfeed_core.models import ArticleReview
from devfeed_core.services import OperationConflict, RecordNotFound

from tests.articles.test_processing_retry_eligibility_unit import active_job, scenario
from tests.support.source_readiness import configuration


@pytest.fixture
def runtime(monkeypatch):
    article, facts = scenario()
    settings = configuration(ai_enabled=True)
    facts["settings"] = settings
    session = Mock()
    session.scalar.return_value = article
    authority = Mock()
    monkeypatch.setattr(retries, "approved_sources", authority)
    monkeypatch.setattr(retries, "get_settings", lambda: settings)
    monkeypatch.setattr(article_lifecycle, "get_settings", lambda: settings)
    monkeypatch.setattr(retries, "_processing_facts", lambda *_: facts)
    commands = {}
    for name, module, attribute in (
        ("enrichment", retries, "request_article_enrichment"),
        ("analysis", analysis, "request_analysis"),
        ("images", image_jobs, "request_image"),
        ("failed", job_retries, "retry_failed_job"),
        ("image_checkpoint", image_jobs, "retry_image"),
    ):
        target = name if name in {"enrichment", "analysis"} else "discovery"
        commands[name] = Mock(return_value=active_job(article, facts, target))
        monkeypatch.setattr(module, attribute, commands[name])
    decision = Mock()
    commands["failed"].side_effect = lambda _session, _job, kind, _actor: active_job(
        article, facts, "analysis" if kind == "analysis" else "enrichment"
    )
    monkeypatch.setattr(editorial, "decide_article", decision)
    return SimpleNamespace(
        article=article,
        facts=facts,
        session=session,
        authority=authority,
        commands=commands,
        decision=decision,
    )


def request(runtime, target="analysis", *, expected_revision=None, expected_job_id=..., actor=None):
    previous = retries.current_processing_job(runtime.facts, target)
    return retries.request_processing_retry(
        runtime.session,
        runtime.article.id,
        target=target,
        expected_revision=runtime.article.editorial_revision
        if expected_revision is None
        else expected_revision,
        expected_job_id=(previous.id if previous else None)
        if expected_job_id is ...
        else expected_job_id,
        actor=actor or {"subject": "editor-123", "name": "Editor"},
    )


@pytest.mark.parametrize("guard", ["missing", "revision", "job-id", "rejected", "active"])
def test_retry_rechecks_identity_revision_and_policy_before_any_enqueue(runtime, guard):
    options = {}
    if guard == "missing":
        runtime.session.scalar.return_value = None
    elif guard == "revision":
        options["expected_revision"] = runtime.article.editorial_revision - 1
    elif guard == "job-id":
        options["expected_job_id"] = uuid.uuid4()
    elif guard == "rejected":
        runtime.article.review_status = "rejected"
    else:
        runtime.facts["analysis"] = active_job(
            runtime.article, runtime.facts, "analysis", status="running"
        )
    with pytest.raises(RecordNotFound if guard == "missing" else OperationConflict):
        request(runtime, **options)
    runtime.authority.assert_called_once_with(runtime.session, runtime.article.id, lock=True)
    assert not any(command.called for command in runtime.commands.values())
    runtime.decision.assert_not_called()
    runtime.session.add.assert_not_called()


@pytest.mark.parametrize("target", ["enrichment", "analysis", "images"])
def test_completed_work_retry_is_independent_and_records_editorial_intent(runtime, target):
    original_jobs = dict(runtime.facts)
    before = (
        runtime.article.review_status,
        runtime.article.publication_status,
        runtime.article.editorial_revision,
    )
    result = request(runtime, target)
    assert result is runtime.article
    command = runtime.commands[target]
    command.assert_called_once_with(
        runtime.session, runtime.article.id, **({"refresh": True} if target == "images" else {})
    )
    assert sum(mock.called for mock in runtime.commands.values()) == 1
    audit = runtime.session.add.call_args.args[0]
    assert isinstance(audit, ArticleReview)
    assert audit.action == "retry" and audit.revision == before[2]
    assert audit.actor == "Editor"
    assert audit.automation == {
        "kind": "processing_retry",
        "target": target,
        "from_stage": "ready",
        "previous_job_id": str(original_jobs[target].id) if target != "images" else None,
        "actor": {"subject": "editor-123", "name": "Editor"},
        "job_id": str(command.return_value.id),
    }
    assert before == (
        runtime.article.review_status,
        runtime.article.publication_status,
        runtime.article.editorial_revision,
    )
    assert all(runtime.facts[key] is value for key, value in original_jobs.items())
    runtime.decision.assert_not_called()
    runtime.session.flush.assert_called_once()


@pytest.mark.parametrize("target", ["enrichment", "analysis", "images"])
def test_retryable_failure_uses_its_checkpoint_instead_of_creating_parallel_work(runtime, target):
    key = "discovery" if target == "images" else target
    failed = active_job(runtime.article, runtime.facts, key, status="failed")
    runtime.facts[key] = failed
    # Repository has confirmed this terminal job's retry eligibility, not evaluated SQL in Python.
    runtime.session.scalar.side_effect = [runtime.article, failed.id]
    request(runtime, target)
    chosen = "image_checkpoint" if target == "images" else "failed"
    if target == "images":
        runtime.commands[chosen].assert_called_once_with(runtime.session, failed.id, refresh=True)
    else:
        runtime.commands[chosen].assert_called_once_with(
            runtime.session,
            failed,
            "article-enrichment" if target == "enrichment" else "analysis",
            {"subject": "editor-123", "name": "Editor"},
        )
    assert sum(mock.called for mock in runtime.commands.values()) == 1
    assert runtime.session.add.call_args.args[0].automation["previous_job_id"] == str(failed.id)


def test_obsolete_image_checkpoint_still_starts_the_explicit_refresh(runtime):
    failed = active_job(runtime.article, runtime.facts, "discovery", status="failed")
    runtime.facts["discovery"] = failed
    runtime.session.scalar.side_effect = [runtime.article, failed.id]
    runtime.commands["image_checkpoint"].return_value = None
    request(runtime, "images")
    runtime.commands["image_checkpoint"].assert_called_once_with(
        runtime.session, failed.id, refresh=True
    )
    runtime.commands["images"].assert_called_once_with(
        runtime.session, runtime.article.id, refresh=True
    )
    assert runtime.session.add.call_args.args[0].automation["job_id"] == str(
        runtime.commands["images"].return_value.id
    )


def test_nonretryable_failure_uses_new_analysis_admission(runtime):
    failed = active_job(runtime.article, runtime.facts, "analysis", status="failed")
    runtime.facts["analysis"] = failed
    runtime.session.scalar.side_effect = [runtime.article, None]
    request(runtime)
    runtime.commands["failed"].assert_not_called()
    runtime.commands["analysis"].assert_called_once_with(runtime.session, runtime.article.id)


@pytest.mark.parametrize(
    "actor,expected",
    [
        ({"name": "Operator", "email": "editor@example.org", "subject": "uid"}, "Operator"),
        ({"email": "editor@example.org", "subject": "uid"}, "editor@example.org"),
        ({"subject": "uid"}, "uid"),
        ({"issuer": "identity-provider"}, "Administrator"),
        ({"name": "E" * 220}, "E" * 200),
    ],
)
def test_retry_audit_uses_the_authenticated_actor_and_bounds_display_name(runtime, actor, expected):
    request(runtime, actor=actor)
    audit = runtime.session.add.call_args.args[0]
    assert audit.actor == expected and audit.automation["actor"] == actor


def test_published_article_is_withdrawn_before_queuing_and_audited_at_new_revision(runtime):
    assert (
        article_lifecycle.derive_publication_readiness(runtime.article, **runtime.facts).stage
        == "ready"
    )
    runtime.article.publication_status = "published"
    runtime.article.published_to_feed_at = runtime.facts["now"]
    revision = runtime.article.editorial_revision
    events = []

    def unpublish(session, identifier, decision, **options):
        assert session is runtime.session and identifier == runtime.article.id
        assert decision.action == "unpublish" and decision.expected_revision == revision
        assert options["automation"]["from_stage"] == "published"
        runtime.article.publication_status = "unpublished"
        runtime.article.editorial_revision += 1
        events.append("unpublish")

    def queue(*_):
        assert runtime.article.publication_status == "unpublished"
        events.append("queue")
        return active_job(runtime.article, runtime.facts, "analysis")

    runtime.decision.side_effect = unpublish
    runtime.commands["analysis"].side_effect = queue
    request(runtime)
    assert events == ["unpublish", "queue"]
    audit = runtime.session.add.call_args.args[0]
    assert audit.revision == revision + 1 and audit.automation["from_stage"] == "published"
    assert runtime.article.review_status == "approved"


def test_refused_enqueue_does_not_record_a_successful_retry(runtime):
    runtime.commands["analysis"].return_value = None
    with pytest.raises(OperationConflict, match="No processing task could be queued"):
        request(runtime)
    runtime.session.add.assert_not_called()
