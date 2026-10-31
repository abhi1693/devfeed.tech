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
from tests.support.article_maintenance_unit import prepared as prepared
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
    runtime.session.scalar.side_effect = [runtime.article, runtime.article, failed.id]
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
    runtime.session.scalar.side_effect = [runtime.article, runtime.article, failed.id]
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
    runtime.session.scalar.side_effect = [runtime.article, runtime.article, None]
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


def test_disabled_published_maintenance_preserves_approval_and_refuses_enqueue(runtime):
    assert (
        article_lifecycle.derive_publication_readiness(runtime.article, **runtime.facts).stage
        == "ready"
    )
    runtime.article.publication_status = "published"
    runtime.article.published_to_feed_at = runtime.facts["now"]
    revision = runtime.article.editorial_revision
    with pytest.raises(OperationConflict, match="disabled"):
        request(runtime)
    assert (
        runtime.article.review_status == "approved"
        and runtime.article.publication_status == "published"
    )
    assert runtime.article.editorial_revision == revision
    assert runtime.article.published_to_feed_at == runtime.facts["now"]
    runtime.decision.assert_not_called()
    assert not any(command.called for command in runtime.commands.values())
    runtime.session.add.assert_not_called()


def test_publication_racing_an_ordinary_retry_requires_fresh_maintenance_admission(runtime):
    from devfeed_core.models import Article

    published = Article(
        id=runtime.article.id,
        editorial_revision=runtime.article.editorial_revision,
        publication_status="published",
        review_status="approved",
    )
    runtime.session.scalar.side_effect = [runtime.article, published]
    with pytest.raises(OperationConflict, match="Publication changed"):
        request(runtime)
    assert not any(command.called for command in runtime.commands.values())
    runtime.session.add.assert_not_called()


def test_refused_enqueue_does_not_record_a_successful_retry(runtime):
    runtime.commands["analysis"].return_value = None
    with pytest.raises(OperationConflict, match="No processing task could be queued"):
        request(runtime)
    runtime.session.add.assert_not_called()


@pytest.fixture
def published_runtime(prepared, monkeypatch):
    from devfeed_core import article_refresh, article_refresh_processing
    from devfeed_core.config import get_settings
    from devfeed_core.models import ArticleAnalysisJob, ArticleEnrichmentJob, ArticleImageJob

    public, owner, inputs, base = prepared
    session = Mock()
    session.scalar.side_effect = [public, None, public]
    monkeypatch.setattr(get_settings(), "published_article_refresh_enabled", True)
    monkeypatch.setattr(retries, "_processing_facts", lambda *_args: inputs.facts)
    monkeypatch.setattr(article_refresh, "prepare_refresh_base", lambda *_args: base)
    initial, private = Mock(return_value=inputs), Mock(return_value=inputs)
    monkeypatch.setattr(article_refresh_processing, "prepare_initial_refresh_inputs", initial)
    monkeypatch.setattr(article_refresh_processing, "prepare_refresh_inputs", private)
    create = Mock(return_value=owner)
    monkeypatch.setattr(article_refresh, "create_refresh", create)
    authority = Mock(return_value=[public.origins[0].source_id])
    monkeypatch.setattr(retries, "approved_sources", authority)
    jobs = {
        "analysis": ArticleAnalysisJob(
            id=uuid.uuid4(),
            article_id=public.id,
            refresh_id=owner.id,
            status="queued",
            attempts=0,
            input_hash=inputs.input_hash,
            steps={},
        ),
        "enrichment": ArticleEnrichmentJob(
            id=uuid.uuid4(), article_id=public.id, refresh_id=owner.id
        ),
        "images": ArticleImageJob(id=uuid.uuid4(), article_id=public.id, refresh_id=owner.id),
    }
    queued = {name: Mock(return_value=value) for name, value in jobs.items()}
    monkeypatch.setattr(article_refresh_processing, "request_refresh_analysis", queued["analysis"])
    monkeypatch.setattr(retries, "request_article_enrichment", queued["enrichment"])
    monkeypatch.setattr(image_jobs, "request_image", queued["images"])
    image_retry = Mock(return_value=jobs["images"])
    monkeypatch.setattr(image_jobs, "retry_image", image_retry)
    current_image = Mock(return_value=owner)
    monkeypatch.setattr(image_jobs, "current_refresh_image", current_image)
    return SimpleNamespace(
        public=public,
        owner=owner,
        inputs=inputs,
        base=base,
        session=session,
        authority=authority,
        create=create,
        queued=queued,
        jobs=jobs,
        initial=initial,
        private=private,
        image_retry=image_retry,
        current_image=current_image,
    )


def published_request(r, target, **overrides):
    previous = retries.current_processing_job(r.inputs.facts, target)
    values = {
        "target": target,
        "expected_revision": r.public.editorial_revision,
        "expected_job_id": previous.id if previous is not None else None,
        "actor": {"subject": "editor", "name": "Editor"},
        **overrides,
    }
    return retries.request_processing_retry(r.session, r.public.id, **values)


@pytest.mark.parametrize("target", ["enrichment", "analysis", "images"])
@pytest.mark.parametrize("existing", [False, True])
def test_published_retry_binds_new_work_without_withdrawing_or_bumping_the_approval(
    published_runtime, target, existing
):
    r = published_runtime
    r.owner.target = "enrichment" if existing else target
    r.session.scalar.side_effect = [r.public, r.owner if existing else None, r.public]
    revision = r.public.editorial_revision
    assert published_request(r, target) is r.public
    assert r.public.publication_status == "published" and r.public.editorial_revision == revision
    assert sum(command.called for command in r.queued.values()) == 1
    assert r.create.call_args.args[3] == ("enrichment" if existing else target)
    if target == "analysis":
        r.queued[target].assert_called_once_with(r.session, r.owner.id, prepared=r.inputs)
    elif target == "enrichment":
        r.queued[target].assert_called_once_with(r.session, r.public.id, refresh_id=r.owner.id)
    else:
        r.queued[target].assert_called_once_with(
            r.session, r.public.id, refresh=True, refresh_id=r.owner.id
        )
    audit = r.session.add.call_args.args[0]
    assert audit.revision == revision and audit.automation["refresh_id"] == str(r.owner.id)
    assert audit.automation["job_id"] == str(r.jobs[target].id)
    assert (r.private.called, r.initial.called) == (existing, not existing)


@pytest.mark.parametrize(
    "guard", ["revision", "job", "target", "source", "missing", "version", "no_job"]
)
def test_published_maintenance_rechecks_input_identity_authority_and_admission_result(
    published_runtime, monkeypatch, guard
):
    r = published_runtime
    options = {}
    if guard == "revision":
        options["expected_revision"] = r.public.editorial_revision - 1
    elif guard == "job":
        options["expected_job_id"] = uuid.uuid4()
    elif guard == "target":
        monkeypatch.setattr(retries, "processing_retry_targets", lambda *_args: [])
    elif guard == "source":
        r.authority.return_value = []
    elif guard == "missing":
        r.session.scalar.side_effect = [r.public, None, None]
    elif guard == "version":
        r.owner.version += 1
    else:
        r.queued["analysis"].return_value = None
    with pytest.raises(RecordNotFound if guard == "missing" else OperationConflict):
        published_request(r, "analysis", **options)
    assert r.public.publication_status == "published"
    r.session.add.assert_not_called()


def test_private_downstream_owner_cannot_be_repurposed_for_an_upstream_retry(published_runtime):
    r = published_runtime
    r.owner.target = "analysis"
    r.session.scalar.side_effect = [r.public, r.owner, r.public]
    with pytest.raises(OperationConflict, match="Another maintenance target"):
        published_request(r, "enrichment")
    assert not any(command.called for command in r.queued.values())


def test_selected_private_failed_analysis_reuses_only_its_valid_independent_steps(
    published_runtime,
):
    r = published_runtime
    r.session.scalar.side_effect = [r.public, r.owner, r.public]
    previous = r.inputs.facts["analysis"]
    previous.status = "failed"
    previous.steps = {
        "language": {"status": "succeeded", "output": {"language": "en"}},
        "description": {"status": "failed"},
    }
    published_request(r, "analysis")
    assert r.jobs["analysis"].steps == {"language": previous.steps["language"]}


@pytest.mark.parametrize("checkpoint", ["matching", "obsolete", "unselected_context"])
def test_published_image_retry_keeps_the_selected_private_original_and_variant_checkpoints(
    published_runtime, checkpoint
):
    from devfeed_core.models import ArticleImageJob

    r = published_runtime
    r.owner.target = "images"
    previous = ArticleImageJob(
        id=uuid.uuid4(),
        article_id=r.public.id,
        refresh_id=r.owner.id,
        operation="discover",
        status="failed",
        storage={"input_url": r.public.canonical_url},
    )
    r.owner.discovery_job_id = previous.id
    r.inputs.facts["discovery"] = previous
    r.session.scalar.side_effect = [r.public, r.owner, r.public]
    if checkpoint == "obsolete":
        r.image_retry.return_value = None
    elif checkpoint == "unselected_context":
        r.current_image.return_value = None
    published_request(r, "images")
    if checkpoint != "unselected_context":
        r.image_retry.assert_called_once_with(r.session, previous.id, refresh=True)
    else:
        r.image_retry.assert_not_called()
    assert r.queued["images"].called == (checkpoint != "matching")
