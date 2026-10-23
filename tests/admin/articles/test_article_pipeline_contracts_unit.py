"""Operator cards preserve admission rules, actionable progress, and private evidence."""

import copy
import uuid
from datetime import timedelta

import pytest
from devfeed_admin_api import article_pipeline as pipeline
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.models import ArticleContent, ArticleOrigin, Source

from tests.support.article_readiness import NOW, image_job
from tests.support.article_readiness import processing_case as processing_case


def card_facts(facts):
    return {
        name: facts[name] for name in ("content", "enrichment", "analysis", "discovery", "storage")
    }


def pending_analysis(job, *, status="queued", steps=None, active=None):
    job.id = uuid.uuid4()
    job.status, job.outcome, job.finished_at = status, None, None
    job.available_at = (
        NOW + timedelta(seconds=30) if status == "queued" else NOW - timedelta(minutes=1)
    )
    if status == "running":
        job.lease_token, job.lease_until = uuid.uuid4(), NOW + timedelta(minutes=3)
    elif status == "failed":
        job.finished_at, job.error = NOW, "step_failed"
    job.usage = {"article_steps": steps or {}}
    if active is not None:
        job.active_step = active
    return job


def test_pending_analysis_displays_specialist_progress_and_due_time(processing_case):
    _, facts = processing_case
    job = pending_analysis(
        facts["analysis"],
        steps={
            "developer_relevance": {"status": "succeeded", "attempts": 1},
            "page_kind": {"status": "succeeded", "attempts": 1},
            "language": {
                "status": "pending",
                "attempts": 2,
                "error": "codex_unavailable",
                "started_at": (NOW - timedelta(minutes=1)).isoformat(),
                "finished_at": (NOW - timedelta(seconds=40)).isoformat(),
            },
        },
        active="language",
    )

    rows = {step.name: step for step in pipeline._analysis_steps(job)}

    assert rows["developer_relevance"].status == "succeeded"
    language = rows["language"]
    assert language.label == "Source language" and language.attempts == 2
    assert language.error == "codex_unavailable" and language.available_at == job.available_at
    assert language.started_at == NOW - timedelta(minutes=1)
    assert language.finished_at == NOW - timedelta(seconds=40)
    assert rows["description"].status == "pending"
    assert pipeline._current_analysis_step(job) == "language"


@pytest.mark.parametrize(
    "error", ["provider response: private-token", {"secret": "token"}, "x" * 101]
)
def test_malformed_legacy_step_metadata_cannot_expose_provider_details(processing_case, error):
    _, facts = processing_case
    job = pending_analysis(
        facts["analysis"],
        steps={
            "language": {
                "status": "unrecognized-provider-state",
                "attempts": -2,
                "error": error,
                "available_at": "invalid-date",
                "started_at": 123,
                "finished_at": None,
            }
        },
        active="language",
    )

    rows = {row.name: row for row in pipeline._analysis_steps(job)}
    language = rows["language"]

    assert language.status == "pending" and language.attempts == 0
    assert language.error == "step_failed"
    assert language.available_at == job.available_at
    assert language.started_at is None and language.finished_at is None
    assert "private-token" not in language.model_dump_json()


def test_completed_and_legacy_runs_do_not_invent_pending_specialist_work(processing_case):
    _, facts = processing_case
    job = facts["analysis"]
    assert pipeline._analysis_steps(None) == []
    assert pipeline._current_analysis_step(None) is None
    assert all(row.status == "skipped" for row in pipeline._analysis_steps(job))
    assert pipeline._current_analysis_step(job) is None
    job.prompt_version = "legacy-analysis-contract"
    assert pipeline._analysis_steps(job) == []
    job.status = "failed"
    assert pipeline._current_analysis_step(job) is None


@pytest.mark.parametrize("state", ["running", "failed", "pending"])
def test_legacy_progress_without_active_marker_finds_the_unfinished_step(processing_case, state):
    _, facts = processing_case
    job = pending_analysis(
        facts["analysis"],
        status="running" if state == "running" else "failed" if state == "failed" else "queued",
        steps={
            "developer_relevance": {"status": "succeeded"},
            "page_kind": {"status": state},
        },
    )
    assert pipeline._current_analysis_step(job) == "page_kind"


@pytest.mark.parametrize("current", ["topics", "retired-step"])
def test_lightweight_projection_only_exposes_known_specialist_steps(processing_case, current):
    _, facts = processing_case
    job = pending_analysis(facts["analysis"])
    job._pipeline_current_step = current
    assert pipeline._current_analysis_step(job) == ("topics" if current == "topics" else None)


@pytest.mark.parametrize("eligible,allow_retry", [(True, True), (True, False), (False, True)])
def test_failed_job_retry_control_requires_both_current_eligibility_and_permission(
    processing_case, eligible, allow_retry
):
    _, facts = processing_case
    job = facts["enrichment"]
    job.status, job.outcome, job.error = "failed", None, "fetch_unavailable"
    job._pipeline_retryable = eligible

    view = pipeline._job(job, retryable=allow_retry)

    assert view.id == job.id and view.status == "failed"
    assert view.retryable is (eligible and allow_retry)
    assert pipeline._job(None) is None


@pytest.mark.parametrize("ai_enabled", [False, True])
def test_disabled_ai_hides_analysis_retry_without_hiding_other_failed_work(
    processing_case, monkeypatch, ai_enabled
):
    _, facts = processing_case
    monkeypatch.setattr(facts["settings"], "ai_enabled", ai_enabled)
    for name in ("enrichment", "analysis"):
        facts[name].status, facts[name].outcome = "failed", None
        facts[name]._pipeline_retryable = True
    result = pipeline._jobs(card_facts(facts))
    assert result.enrichment.retryable is True
    assert result.analysis.retryable is ai_enabled


@pytest.mark.parametrize("ai_enabled", [False, True])
def test_missing_analysis_only_offers_advance_when_ai_is_available(
    processing_case, monkeypatch, ai_enabled
):
    article, facts = processing_case
    monkeypatch.setattr(facts["settings"], "ai_enabled", ai_enabled)
    facts["analysis"] = None
    result = derive_publication_readiness(article, **facts)
    assert pipeline._actions(result) == (["advance"] if ai_enabled else [])


@pytest.mark.parametrize("status", ["queued", "running", "failed"])
def test_existing_enrichment_work_cannot_be_duplicated_by_advance(processing_case, status):
    article, facts = processing_case
    facts["enrichment"].status, facts["enrichment"].outcome = status, None
    result = derive_publication_readiness(article, **facts)
    assert pipeline._actions(result) == []


def test_advance_can_start_missing_enrichment_or_refetch_unreadable_completed_evidence(
    processing_case,
):
    article, facts = processing_case
    completed = facts["enrichment"]
    facts["enrichment"] = None
    result = derive_publication_readiness(article, **facts)
    assert result.stage == "ingested" and pipeline._actions(result) == ["advance"]
    facts["enrichment"] = completed
    article.summary = "---"
    result = derive_publication_readiness(article, **facts)
    assert result.stage == "enrichment" and "insufficient_source_text" in result.blockers
    assert pipeline._actions(result) == ["advance"]


@pytest.mark.parametrize("blocked", ["manual", "insufficient-evidence", "outside-window", "source"])
def test_advance_cannot_bypass_review_evidence_or_source_policy(
    processing_case, monkeypatch, blocked
):
    article, facts = processing_case
    if blocked == "manual":
        facts["analysis"] = None
        article.classification_provenance = {
            **article.classification_provenance,
            "origin": "manual",
        }
    elif blocked == "insufficient-evidence":
        facts["analysis"].outcome = "insufficient_evidence"
        facts["analysis"].result = {"outcome": "insufficient_evidence"}
    elif blocked == "outside-window":
        monkeypatch.setattr(facts["settings"], "ai_content_not_before", NOW.date())
        article.published_at = NOW - timedelta(days=1)
    else:
        article.origins[0].source.approval_status = "rejected"
    result = derive_publication_readiness(article, **facts)
    assert result.blockers and pipeline._actions(result) == []


def test_image_recovery_and_editorial_decisions_remain_distinct_actions(processing_case):
    article, facts = processing_case
    facts["enrichment"].result = {"input_url": article.canonical_url}
    missing_image = derive_publication_readiness(article, **facts)
    assert missing_image.stage == "images" and pipeline._actions(missing_image) == ["advance"]
    facts["discovery"] = image_job(article, operation="discover")
    article.review_status = "pending"
    review = derive_publication_readiness(article, **facts)
    assert review.stage == "review" and pipeline._actions(review) == []
    article.review_status = "approved"
    ready = derive_publication_readiness(article, **facts)
    assert ready.stage == "ready" and pipeline._actions(ready) == []


def test_display_cache_changes_at_retry_due_boundary_without_invalidating_every_second(
    processing_case,
):
    article, facts = processing_case
    pending_analysis(facts["analysis"])
    current = card_facts(facts)
    options = {"catalog_hash": "current-catalog", "settings": facts["settings"]}
    before = pipeline._readiness_key(article, current, now=NOW, **options)
    assert (
        pipeline._readiness_key(article, current, now=NOW + timedelta(seconds=10), **options)
        == before
    )
    after = pipeline._readiness_key(article, current, now=NOW + timedelta(seconds=31), **options)
    assert before != after
    assert derive_publication_readiness(article, **facts).state == "retry_wait"
    facts["now"] = NOW + timedelta(seconds=31)
    assert derive_publication_readiness(article, **facts).state == "waiting"


@pytest.mark.parametrize(
    "changed", ["source-revoked", "source-summary", "private-evidence", "catalog"]
)
def test_display_cache_invalidates_when_publication_admission_inputs_change(
    processing_case, changed
):
    article, facts = processing_case
    options = {"catalog_hash": "current-catalog", "settings": facts["settings"], "now": NOW}
    before = pipeline._readiness_key(article, card_facts(facts), **options)
    if changed == "source-revoked":
        article.origins[0].source.approval_status = "rejected"
        blocker = "no_approved_source"
    elif changed == "source-summary":
        article.summary = "Updated Angular source evidence changes the current analysis input."
        blocker = "current_analysis_required"
    elif changed == "private-evidence":
        facts["content"] = ArticleContent(
            article_id=article.id,
            url=article.canonical_url,
            method="article",
            text="Fresh private Angular evidence from the original page.",
            content_hash="new-extraction-hash",
        )
        blocker = "current_analysis_required"
    else:
        facts["taxonomy"] = copy.deepcopy(facts["taxonomy"])
        facts["taxonomy"]["topics"][0]["name"] = "Angular application routing"
        options["catalog_hash"] = "updated-catalog"
        blocker = "current_catalog_required"
    after = pipeline._readiness_key(article, card_facts(facts), **options)
    assert before != after
    assert blocker in derive_publication_readiness(article, **facts).blockers


def test_card_prefers_approved_attribution_and_omits_private_inference_evidence(processing_case):
    article, facts = processing_case
    source = Source(
        id=uuid.uuid4(),
        name="Alpha publisher",
        source_type="publisher",
        approval_status="approved",
        enabled=True,
        feed_url="https://alpha.example/feed",
    )
    unapproved = Source(
        id=uuid.uuid4(),
        name="A directory",
        source_type="aggregator",
        approval_status="pending",
        enabled=False,
        feed_url="https://directory.example/feed",
    )
    article.origins.extend(
        ArticleOrigin(
            source=value,
            source_id=value.id,
            article_id=article.id,
            entry_key=str(article.id),
            original_url=article.canonical_url,
        )
        for value in (unapproved, source)
    )
    article.ai_title = "An identifying Angular routing title"
    result = derive_publication_readiness(article, **facts)
    card = pipeline._card(article, result, card_facts(facts), taxonomy=facts["taxonomy"])
    assert card.source_id == source.id and card.source_name == source.name
    assert card.title == article.ai_title and card.stage == "ready"
    assert card.editorial_revision == article.editorial_revision
    assert article.summary not in card.model_dump_json()
    assert "input_snapshot" not in card.model_dump_json()
    assert "catalog_snapshot" not in card.model_dump_json()


def test_card_keeps_unattributed_candidates_visible_without_offering_writes(processing_case):
    article, facts = processing_case
    article.origins = []
    result = derive_publication_readiness(article, **facts)
    card = pipeline._card(article, result, card_facts(facts), taxonomy=facts["taxonomy"])
    assert card.source_id is None and card.source_name is None
    assert "no_approved_source" in card.blockers
    assert card.allowed_actions == card.retry_targets == []
    assert card.next_check_at == article.automation_next_check_at


def test_source_groups_prioritize_active_and_blocked_work_before_name_order():
    groups = [
        pipeline.PipelineSourceGroup(
            source_id=uuid.uuid4(),
            source_name=name,
            total=sum(counts.values()),
            state_counts=counts,
        )
        for name, counts in (
            ("Alpha waiting", {"waiting": 2}),
            ("Retry wait", {"retry_wait": 1}),
            ("Blocked", {"blocked": 1}),
            ("Zulu running", {"running": 1, "waiting": 2}),
        )
    ]
    ordered = sorted(groups, key=pipeline._source_group_order)
    assert [group.source_name for group in ordered] == [
        "Zulu running",
        "Blocked",
        "Retry wait",
        "Alpha waiting",
    ]


@pytest.fixture
def operator_detail(processing_case, monkeypatch):
    article, facts = processing_case

    def loaded_facts(session, articles):
        assert [item.id for item in articles] == [article.id]
        return {
            name: {article.id: value} if value is not None else {}
            for name, value in card_facts(facts).items()
        }

    # The test supplies the read boundary's preloaded ORM rows; no SQL is simulated.
    monkeypatch.setattr(pipeline, "_batch_facts", loaded_facts)
    monkeypatch.setattr(pipeline, "catalog", lambda _: facts["taxonomy"])
    return lambda: pipeline._detail(object(), article)


@pytest.mark.parametrize(
    "scenario,check,status,detail,stage,label",
    [
        ("ready", "images", "passed", "reader fallback", "ready", None),
        ("approval", "approval", "pending", "not approved", "review", None),
        ("source", "source", "blocked", "no approved source", "review", None),
        (
            "disabled-ai",
            "ai_not_configured",
            "blocked",
            "Configure and enable AI",
            "analysis",
            None,
        ),
        ("paywall", "paywalled_content", "blocked", "paywall", "enrichment", None),
        (
            "insufficient-analysis",
            "insufficient_analysis_evidence",
            "blocked",
            "Inspect the source",
            "analysis",
            None,
        ),
        (
            "date-window",
            "content_date_deferred",
            "blocked",
            "outside the configured AI content window",
            "analysis",
            None,
        ),
        (
            "empty-source",
            "insufficient_source_text",
            "blocked",
            "No readable source text",
            "enrichment",
            "Fetch source again",
        ),
        ("missing-enrichment", "enrichment", "pending", "missing", "ingested", "Start enrichment"),
    ],
)
def test_operator_checklist_explains_admission_and_offers_only_the_safe_next_action(
    processing_case, operator_detail, monkeypatch, scenario, check, status, detail, stage, label
):
    article, facts = processing_case
    if scenario == "approval":
        article.review_status = "pending"
    elif scenario == "source":
        article.origins[0].source.approval_status = "rejected"
    elif scenario == "disabled-ai":
        facts["analysis"] = None
        monkeypatch.setattr(facts["settings"], "ai_enabled", False)
    elif scenario == "paywall":
        facts["enrichment"].result = {**facts["enrichment"].result, "paywalled": True}
    elif scenario == "insufficient-analysis":
        facts["analysis"].outcome = "insufficient_evidence"
        facts["analysis"].result = {"outcome": "insufficient_evidence"}
    elif scenario == "date-window":
        article.published_at = NOW - timedelta(days=1)
        monkeypatch.setattr(facts["settings"], "ai_content_not_before", NOW.date())
    elif scenario == "empty-source":
        article.summary = "---"
    elif scenario == "missing-enrichment":
        facts["enrichment"] = None

    result = operator_detail()

    checks = {item.code: item for item in result.checks}
    assert checks[check].status == status and detail in checks[check].detail
    assert result.stage == stage
    assert result.can_publish is (scenario == "ready")
    assert result.advance_label == label
    assert result.allowed_actions == (["advance"] if label else [])


@pytest.mark.parametrize("terminal", ["published", "rejected"])
def test_terminal_detail_keeps_completed_history_without_reopening_publication(
    processing_case, operator_detail, terminal
):
    article, _ = processing_case
    if terminal == "published":
        article.publication_status = "published"
        article.published_to_feed_at = NOW
    else:
        article.review_status = "rejected"

    result = operator_detail()

    assert result.stage == terminal and result.state == "complete"
    assert not result.can_publish and not result.allowed_actions and result.advance_label is None
    assert result.checks == []
    assert result.jobs.enrichment.retryable is False and result.jobs.analysis.retryable is False
