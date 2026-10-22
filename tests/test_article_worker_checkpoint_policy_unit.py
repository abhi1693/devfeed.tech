"""Specialist checkpoints respect current evidence, policy and invocation ownership."""

import copy
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_aggregator import analysis_tasks as worker
from devfeed_core import ai_content, article_automation
from devfeed_core.analysis import snapshot_hash
from devfeed_core.article_analysis_steps import STEP_SPECS, prepare_article_step
from devfeed_core.job_lifecycle import fail_or_retry, start_job
from devfeed_core.models import ArticleAnalysisJob
from test_analysis_admission_contracts_unit import admission as admission
from test_source_readiness_unit import configuration


@pytest.fixture
def service(admission, monkeypatch):
    v = admission
    v.job = copy.deepcopy(v.prior)
    v.job.id, v.job.status, v.job.attempts = uuid.uuid4(), "queued", 0
    v.job.result, v.job.steps, v.job.outcome = {}, {}, None
    v.job.finished_at, v.job.created_at = None, v.facts["now"] - timedelta(minutes=1)
    v.job.available_at = v.job.created_at
    v.job.error = None
    token = start_job(v.job, v.job.created_at, 300)
    v.session.scalar.side_effect = lambda statement: (
        v.job if statement.column_descriptions[0]["entity"] is ArticleAnalysisJob else v.article
    )
    v.factory = MagicMock()
    v.factory.return_value.__enter__.return_value = v.session
    v.factory.begin.return_value.__enter__.return_value = v.session
    monkeypatch.setattr(worker, "utcnow", lambda: v.facts["now"])
    monkeypatch.setattr(worker, "approved_sources", v.approved)
    monkeypatch.setattr(worker, "catalog", lambda _: v.taxonomy)
    v.settings = configuration(
        ai_enabled=True, codex_model="unit-model", ai_content_not_before=None
    )
    monkeypatch.setattr(ai_content, "get_settings", lambda: v.settings)
    v.service = worker.ArticleAnalysisService(v.settings, v.factory, Mock())
    v.context = worker.AnalysisContext(
        v.job.id, token, copy.deepcopy(v.job.input_snapshot), v.article.id
    )
    v.attempt = worker.AnalysisAttempt(number=1)
    return v


@pytest.mark.parametrize(
    "guard",
    ["unapproved", "rejected", "editorial-edit", "source-edit", "outside-window"],
)
def test_each_paid_step_rechecks_source_editorial_and_publication_admission(
    service, monkeypatch, guard
):
    v = service
    refresh = Mock()
    monkeypatch.setattr(worker, "refresh_superseded_analysis", refresh)
    if guard == "unapproved":
        v.approved.return_value = []
    elif guard == "rejected":
        v.article.review_status = "rejected"
    elif guard == "editorial-edit":
        v.article.editorial_revision += 1
    elif guard == "source-edit":
        v.article.summary += " Updated source evidence."
    else:
        v.settings.ai_content_not_before = date(2026, 1, 1)
        v.article.published_at = v.facts["now"].replace(year=2025)
    digest = prepare_article_step("developer_relevance", v.context.snapshot, v.taxonomy, {})[2]
    assert v.service.start_step(v.context, "developer_relevance", digest) is None
    assert v.job.status == "succeeded"
    assert v.job.outcome == (
        "unapproved"
        if guard == "unapproved"
        else "content_date_deferred"
        if guard == "outside-window"
        else "superseded"
    )
    assert v.job.steps == {} and v.job.lease_token is None
    assert refresh.call_count == int(guard in {"rejected", "editorial-edit", "source-edit"})
    v.service.client_factory.assert_not_called()


@pytest.mark.parametrize("same", [False, True])
def test_step_retry_preserves_feedback_only_when_its_exact_context_is_unchanged(service, same):
    v = service
    name = "developer_relevance"
    digest = prepare_article_step(name, v.context.snapshot, v.taxonomy, {})[2]
    previous = {
        "status": "pending",
        "attempts": 1,
        "input_hash": digest
        if same
        else snapshot_hash({**v.context.snapshot, "text": "Older evidence"}),
        "prompt_version": STEP_SPECS[name].prompt_version,
        "model": v.settings.codex_model,
        "routing_binding": v.service.routing_binding(),
        "validation_feedback": {
            "code": "invalid_analysis_result",
            "fields": ["developer_relevance"],
        },
        "error": "invalid_analysis_result",
        "output": None,
        "started_at": (v.facts["now"] - timedelta(seconds=50)).isoformat(),
        "finished_at": (v.facts["now"] - timedelta(seconds=49)).isoformat(),
    }
    v.job.steps = {name: previous}
    fail_or_retry(v.job, "invalid_analysis_result", v.facts["now"] - timedelta(seconds=49))
    replacement = start_job(v.job, v.facts["now"], 300)
    v.context = worker.AnalysisContext(v.job.id, replacement, v.context.snapshot, v.article.id)
    created = v.service.start_step(v.context, name, digest)
    assert created["status"] == "running" and created["attempts"] == (2 if same else 1)
    assert ("validation_feedback" in created) is same
    assert created["input_hash"] == digest and created["output"] is None
    assert created["error"] is None and created["finished_at"] is None
    assert v.job.steps["identifying_title"]["status"] == "skipped"
    assert v.job.usage["active_step"] == name
    assert v.job.lease_until >= v.facts["now"] + timedelta(minutes=5)


@pytest.mark.parametrize(
    "guard", ["manual", "unrelated", "utility", "unapproved", "rejected", "valid"]
)
def test_topic_proposals_require_both_full_automation_and_current_source_authority(
    service, monkeypatch, guard
):
    v = service
    settings = configuration(ai_enabled=True, full_automation=guard != "manual")
    current = worker.ArticleAnalysisService(settings, v.factory, Mock())
    result = copy.deepcopy(v.prior.result)
    if guard == "unrelated":
        result.update(developer_relevance="unrelated", topics=[])
    if guard == "utility":
        result.update(page_kind="non_article", topics=[])
    from devfeed_core.analysis import AnalysisResult

    parsed = AnalysisResult.model_validate(result)
    propose = Mock()
    monkeypatch.setattr(article_automation, "propose_source_topics", propose)
    if guard == "unapproved":
        v.approved.return_value = []
    if guard == "rejected":
        v.article.review_status = "rejected"
    current.propose_topics(v.context, parsed)
    if guard == "valid":
        propose.assert_called_once_with(v.session, v.article)
    else:
        propose.assert_not_called()
    assert v.article.publication_status == "unpublished"


@pytest.mark.parametrize("changes", [1, 2, 3])
def test_application_reprepares_changed_inputs_with_a_bounded_retry_without_repeating_inference(
    service,
    monkeypatch,
    changes,
):
    v = service
    prepare = Mock(return_value=(v.taxonomy, v.context.snapshot))
    monkeypatch.setattr(worker, "_prepare_application", prepare)
    outcomes = [worker._ApplicationInputsChanged() for _ in range(changes)]
    if changes < 3:
        outcomes.append("applied")
    operation = Mock(side_effect=outcomes)
    if changes == 3:
        with pytest.raises(worker._ApplicationInputsChanged):
            worker._apply_prepared(v.factory, v.article.id, v.context.snapshot, operation)
    else:
        assert (
            worker._apply_prepared(v.factory, v.article.id, v.context.snapshot, operation)
            == "applied"
        )
    assert prepare.call_count == operation.call_count == min(3, changes + 1)
    v.service.client_factory.assert_not_called()


@pytest.mark.parametrize("same", [False, True])
def test_aggregate_validation_feedback_retains_the_original_failure_and_detects_repeated_output(
    service, same
):
    v = service
    output = {"ai_summary": "Unsupported output"}
    old = output if same else {"ai_summary": "Different unsupported output"}
    v.job.usage = {**v.job.usage, "invalid_output_hash": snapshot_hash(old)}
    v.job.result = {"topic_match_status": "no_topic_match"}
    attempt = worker.AnalysisAttempt(output=output)
    feedback = {"code": "missing_ai_description", "fields": ["ai_description"]}
    assert v.service.save_feedback(v.job, attempt, feedback) is same
    assert v.job.result == {"topic_match_status": "no_topic_match", "validation_feedback": feedback}
    assert v.job.usage["invalid_output_hash"] == snapshot_hash(output)
