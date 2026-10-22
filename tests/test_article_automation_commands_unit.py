"""Automation decisions over repository results; SQL eligibility has integration coverage."""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import ai_content, analysis
from devfeed_core import article_automation as automation
from devfeed_core.article_jobs import EXTRACTION_VERSION
from devfeed_core.article_lifecycle import derive_processing_readiness
from devfeed_core.editorial import publication_blockers
from devfeed_core.models import ArticleAnalysisJob, ArticleContent
from test_processing_retry_eligibility_unit import active_job, scenario
from test_source_readiness_unit import configuration


def factory_for(read, write):
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=read)
    factory.return_value.__exit__ = Mock(return_value=False)
    factory.begin.return_value.__enter__ = Mock(return_value=write)
    factory.begin.return_value.__exit__ = Mock(return_value=False)
    return factory


@pytest.fixture
def runtime(monkeypatch):
    article, facts = scenario()
    now = facts["now"]
    article.review_status = "pending"
    article.editorial_revision = 0
    article.automation_started_at = None
    article.automation_next_check_at = now - timedelta(minutes=1)
    article.origins[0].source.publication_policy = "auto"
    facts["enrichment"].result.update(extraction_version=EXTRACTION_VERSION, text_source="body")
    facts["analysis"].usage = {}
    settings = configuration(ai_enabled=True, full_automation=True, ai_content_not_before=None)
    read, write = Mock(), Mock()
    read.scalars.return_value.all.return_value = [article.id]
    value = SimpleNamespace(
        article=article,
        facts=facts,
        now=now,
        read=read,
        write=write,
        settings=settings,
        factory=factory_for(read, write),
        prepared=True,
        policy_status="published",
        pending=False,
    )
    monkeypatch.setattr(automation, "get_settings", lambda: value.settings)
    monkeypatch.setattr(ai_content, "get_settings", lambda: value.settings)
    monkeypatch.setattr(automation, "utcnow", lambda: now)
    monkeypatch.setattr(automation, "reject_known_paywalled_articles", lambda _: 0)

    def prepare(*_):
        return (
            (
                facts["taxonomy"],
                automation.source_snapshot(article, getattr(value, "content", None)),
            )
            if value.prepared
            else None
        )

    monkeypatch.setattr(automation, "_prepare_article_candidates", prepare)
    value.authority, value.reconcile, value.lock = Mock(), Mock(), Mock()
    monkeypatch.setattr(automation, "approved_sources", value.authority)
    monkeypatch.setattr(automation, "reconcile_processing", value.reconcile)
    monkeypatch.setattr(automation, "lock_topics", value.lock)
    value.catalog_current = Mock(return_value=True)
    monkeypatch.setattr(automation, "snapshot_current", value.catalog_current)
    value.propose = Mock(return_value=0)
    monkeypatch.setattr(automation, "propose_source_topics", value.propose)
    value.evaluate = Mock(return_value={"status": "would_publish", "reasons": []})
    monkeypatch.setattr(automation, "evaluate_publication", value.evaluate)

    def apply(*_, **__):
        if value.policy_status == "published":
            article.publication_status = "published"
            article.review_status = "approved"
            article.published_to_feed_at = now
        elif value.policy_status == "rejected":
            article.review_status = "rejected"
        return {"status": value.policy_status, "reasons": []}

    value.apply = Mock(side_effect=apply)
    monkeypatch.setattr(automation, "apply_publication_policy", value.apply)
    value.decision = Mock()
    monkeypatch.setattr(automation, "decide_article", value.decision)
    value.enrich = Mock()
    monkeypatch.setattr(automation, "request_article_enrichment", value.enrich)
    value.analyze = Mock(
        return_value=ArticleAnalysisJob(
            id=uuid.uuid4(),
            article_id=article.id,
            status="queued",
            usage={},
            attempts=0,
            created_at=now,
            available_at=now,
        )
    )
    monkeypatch.setattr(automation, "request_analysis", value.analyze)
    value.topic_match = Mock(side_effect=lambda *_: value.pending)
    monkeypatch.setattr(automation, "pending_topic_matches", value.topic_match)
    return value


def run(value):
    value.write.scalar.side_effect = [
        value.facts["analysis"],
        value.article,
        value.facts["enrichment"],
    ]
    value.write.get.return_value = getattr(value, "content", None)
    return automation.schedule_article_automation(value.factory)


def test_current_ready_analysis_passes_evaluation_and_counts_successful_publication(runtime):
    assert derive_processing_readiness(runtime.article, **runtime.facts).stage == "review"
    assert publication_blockers(runtime.article) == ["not_approved"]
    runtime.propose.return_value = 2
    counts = run(runtime)
    assert counts == {
        "articles_checked": 1,
        "articles_published": 1,
        "articles_rejected": 0,
        "source_topics_proposed": 2,
    }
    assert runtime.article.automation_started_at == runtime.now
    assert runtime.article.automation_next_check_at == runtime.now + automation.CHECK_INTERVAL
    assert runtime.article.publication_status == "published"
    runtime.reconcile.assert_called_once_with(
        runtime.write, runtime.article, taxonomy=runtime.facts["taxonomy"]
    )
    assert runtime.apply.call_args.kwargs["evaluation"] is runtime.evaluate.return_value
    runtime.analyze.assert_not_called()
    runtime.enrich.assert_not_called()


@pytest.mark.parametrize(
    "guard",
    [
        "missing",
        "rejected",
        "published",
        "not-due",
        "paused-source",
        "unapproved-source",
        "content-cutoff",
    ],
)
def test_scoped_candidate_is_rechecked_before_any_automation_mutation(runtime, guard):
    if guard == "missing":
        runtime.article = None
    elif guard == "rejected":
        runtime.article.review_status = "rejected"
    elif guard == "published":
        runtime.article.review_status = "approved"
        runtime.article.publication_status = "published"
        runtime.article.published_to_feed_at = runtime.now
    elif guard == "not-due":
        runtime.article.automation_next_check_at = runtime.now + timedelta(minutes=2)
    elif guard == "paused-source":
        runtime.article.origins[0].source.enabled = False
    elif guard == "unapproved-source":
        runtime.article.origins[0].source.approval_status = "pending"
    else:
        runtime.settings = configuration(
            ai_enabled=True,
            full_automation=True,
            ai_content_not_before=(runtime.now + timedelta(days=1)).date(),
        )
    runtime.prepared = False
    counts = run(runtime)
    assert counts["articles_checked"] == counts["articles_published"] == 0
    runtime.reconcile.assert_not_called()
    runtime.evaluate.assert_not_called()
    runtime.apply.assert_not_called()
    runtime.analyze.assert_not_called()


@pytest.mark.parametrize("mode", ["manual", "already-approved"])
def test_reconciliation_does_not_grant_automatic_editorial_authority(runtime, mode):
    if mode == "manual":
        runtime.settings = configuration(ai_enabled=True, full_automation=False)
    else:
        runtime.article.review_status = "approved"
    assert run(runtime)["articles_checked"] == 1
    runtime.reconcile.assert_called_once()
    runtime.evaluate.assert_not_called()
    runtime.apply.assert_not_called()
    runtime.analyze.assert_not_called()


@pytest.mark.parametrize("target", ["analysis", "enrichment"])
@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_work_retains_its_provider_delay_or_lease(runtime, target, status):
    pending = active_job(runtime.article, runtime.facts, target, status=status)
    runtime.facts[target] = pending
    before = (
        pending.status,
        pending.attempts,
        pending.available_at,
        pending.lease_token,
        pending.lease_until,
    )
    assert run(runtime)["articles_checked"] == 1
    assert before == (
        pending.status,
        pending.attempts,
        pending.available_at,
        pending.lease_token,
        pending.lease_until,
    )
    runtime.analyze.assert_not_called()
    runtime.enrich.assert_not_called()
    runtime.apply.assert_not_called()


def test_exhausted_extraction_without_source_text_is_rejected_with_attributed_reason(runtime):
    runtime.facts["enrichment"] = active_job(
        runtime.article, runtime.facts, "enrichment", status="failed"
    )
    failed = runtime.facts["enrichment"]
    failed.error = "Publisher returned no usable text"
    runtime.facts["analysis"] = None
    runtime.article.summary = None
    runtime.prepared = False
    assert run(runtime)["articles_rejected"] == 1
    decision = runtime.decision.call_args.args[2]
    assert (
        decision.action == "reject"
        and decision.expected_revision == runtime.article.editorial_revision
    )
    assert "no publisher summary or article text" in decision.note
    assert runtime.decision.call_args.kwargs["automation"] == {
        "policy": "article-enrichment-failure-v1",
        "job_id": str(failed.id),
        "error": failed.error,
    }
    runtime.analyze.assert_not_called()
    runtime.enrich.assert_not_called()


def test_completed_empty_lookup_stays_pending_without_an_analysis_or_extraction_loop(runtime):
    runtime.facts["analysis"] = None
    runtime.facts["enrichment"].outcome = "not_found"
    runtime.article.summary = None
    runtime.policy_status = "blocked"
    counts = run(runtime)
    assert counts["articles_rejected"] == counts["articles_published"] == 0
    assert runtime.article.review_status == "pending"
    runtime.apply.assert_called_once()
    runtime.decision.assert_not_called()
    runtime.analyze.assert_not_called()
    runtime.enrich.assert_not_called()


@pytest.mark.parametrize("source", ["publisher-summary", "extracted-body"])
def test_exhausted_extraction_keeps_usable_source_evidence_for_analysis(runtime, source):
    runtime.facts["enrichment"] = active_job(
        runtime.article, runtime.facts, "enrichment", status="failed"
    )
    runtime.facts["analysis"] = None
    if source == "extracted-body":
        runtime.content = ArticleContent(
            article_id=runtime.article.id, text=runtime.article.summary, method="readability"
        )
        runtime.article.summary = None
    counts = run(runtime)
    assert counts["articles_rejected"] == 0
    runtime.decision.assert_not_called()
    runtime.enrich.assert_not_called()
    runtime.analyze.assert_called_once()


def test_first_attempt_requests_extraction_before_analysis(runtime):
    runtime.facts.update(analysis=None, enrichment=None)
    runtime.prepared = False
    run(runtime)
    runtime.enrich.assert_called_once_with(runtime.write, runtime.article.id, automatic=True)
    runtime.analyze.assert_not_called()


@pytest.mark.parametrize("changed", ["not-prepared", "source", "catalog"])
def test_changed_preparation_is_reranked_on_next_tick_without_applying_stale_results(
    runtime, monkeypatch, changed
):
    if changed == "not-prepared":
        runtime.prepared = False
    elif changed == "catalog":
        runtime.catalog_current.return_value = False
    else:
        before = copy.deepcopy(automation.source_snapshot(runtime.article, None))
        monkeypatch.setattr(
            automation,
            "_prepare_article_candidates",
            lambda *_: (runtime.facts["taxonomy"], before),
        )
        runtime.article.summary += " Publisher correction changes routing guidance."
    counts = run(runtime)
    assert counts["articles_checked"] == 1 and counts["articles_published"] == 0
    assert runtime.article.automation_next_check_at == runtime.now
    runtime.apply.assert_not_called()
    runtime.analyze.assert_not_called()


@pytest.mark.parametrize("classification", ["unrelated", "non_article"])
def test_conclusive_negative_result_runs_access_policy_without_proposing_new_topics(
    runtime, classification
):
    result = analysis.AnalysisResult(
        developer_relevance="unrelated" if classification == "unrelated" else "relevant",
        page_kind="non_article" if classification == "non_article" else "article",
        language="en",
        content_type="tutorial",
        content_format="article",
        outcome="ready",
        ai_title=None,
        title_evidence=None,
        ai_summary="A publisher article outside software engineering.",
        ai_description="A publisher article outside the developer feed.",
        topics=[],
        tags=[],
        reasons=[],
    )
    runtime.facts["analysis"].result = result.model_dump(mode="json")
    runtime.article.topic_links = []
    runtime.article.ai_title = result.ai_title
    runtime.article.ai_summary = result.ai_summary
    runtime.article.ai_description = result.ai_description
    runtime.article.classification_provenance[
        "developer_relevance" if classification == "unrelated" else "page_kind"
    ] = classification
    runtime.policy_status = "rejected"
    assert run(runtime)["articles_rejected"] == 1
    runtime.propose.assert_not_called()
    runtime.apply.assert_called_once_with(
        runtime.write,
        runtime.article,
        runtime.facts["analysis"],
        taxonomy=runtime.facts["taxonomy"],
    )
    runtime.evaluate.assert_not_called()


@pytest.mark.parametrize("old", ["description", "truncated-code"])
def test_only_affected_legacy_extractions_are_requested_again(runtime, old):
    enrichment = runtime.facts["enrichment"]
    enrichment.result.update(
        extraction_version="old-version",
        text_source="description" if old == "description" else "body",
        sample_characters=2500 if old == "truncated-code" else 300,
    )
    runtime.facts["analysis"] = None
    run(runtime)
    runtime.enrich.assert_called_once_with(runtime.write, runtime.article.id)
    runtime.analyze.assert_not_called()


def test_current_extraction_version_prevents_legacy_reextraction_loop(runtime):
    runtime.facts["enrichment"].result.update(text_source="description", sample_characters=2500)
    runtime.facts["analysis"] = None
    run(runtime)
    runtime.enrich.assert_not_called()
    runtime.analyze.assert_called_once()


@pytest.mark.parametrize("current_validation", [False, True])
def test_title_validation_fix_retries_only_results_from_the_old_contract(
    runtime, current_validation
):
    current = runtime.facts["analysis"]
    current.status, current.outcome, current.error = "failed", None, "invalid_analysis_result"
    runtime.article.classification_provenance = {}
    current.result = {
        "validation_feedback": {"code": "schema_validation", "fields": ["result:value_error"]}
    }
    current.usage = {
        "validation_version": automation.VALIDATION_VERSION if current_validation else "legacy"
    }
    runtime.policy_status = "blocked"
    run(runtime)
    if current_validation:
        runtime.analyze.assert_not_called()
        runtime.apply.assert_called_once()
    else:
        runtime.analyze.assert_called_once_with(
            runtime.write,
            runtime.article.id,
            automatic=True,
            force=True,
            taxonomy=runtime.facts["taxonomy"],
        )
        assert (
            runtime.analyze.return_value.usage["requested_reason"] == "title_validation_corrected"
        )


@pytest.mark.parametrize(
    "stale", ["input", "revision", "catalog", "prompt", "superseded", "content_date_deferred"]
)
def test_stale_analysis_requires_a_fresh_attempt_before_publication(runtime, stale):
    current = runtime.facts["analysis"]
    if stale == "input":
        runtime.article.summary += " Updated publisher explanation of route navigation."
    elif stale == "revision":
        runtime.article.editorial_revision += 1
    elif stale == "catalog":
        current.catalog_snapshot = {"topics": [], "tags": []}
        current.catalog_hash = automation.snapshot_hash(current.catalog_snapshot)
    elif stale == "prompt":
        current.prompt_version = "obsolete-prompt"
    else:
        current.outcome = stale
        runtime.article.classification_provenance = {}
    run(runtime)
    runtime.analyze.assert_called_once_with(
        runtime.write,
        runtime.article.id,
        automatic=True,
        force=True,
        taxonomy=runtime.facts["taxonomy"],
    )
    runtime.evaluate.assert_not_called()
    runtime.apply.assert_not_called()


@pytest.mark.parametrize("match", ["no_topic_match", "no_primary_topic"])
def test_missing_topic_result_blocks_publication_without_rerunning_unchanged_analysis(
    runtime, match
):
    runtime.facts["analysis"].outcome = "insufficient_evidence"
    runtime.facts["analysis"].result["outcome"] = "insufficient_evidence"
    runtime.facts["analysis"].result["topic_match_status"] = match
    runtime.article.classification_provenance = {}
    runtime.policy_status = "blocked"
    run(runtime)
    runtime.evaluate.assert_not_called()
    runtime.apply.assert_called_once()
    runtime.analyze.assert_not_called()


@pytest.mark.parametrize(
    "within_window,pending,wait", [(True, True, True), (False, True, False), (True, False, False)]
)
def test_matching_topic_research_has_a_bounded_wait_before_recording_blocked_decision(
    runtime, within_window, pending, wait
):
    started = runtime.now - timedelta(hours=23 if within_window else 24)
    runtime.article.automation_started_at = started
    runtime.pending = pending
    runtime.evaluate.return_value = {
        "status": "blocked",
        "reasons": ["missing_active_primary_topic"],
    }
    runtime.policy_status = "blocked"
    run(runtime)
    assert runtime.article.automation_started_at == started
    if wait:
        runtime.apply.assert_not_called()
    else:
        runtime.apply.assert_called_once()
    runtime.analyze.assert_not_called()


@pytest.mark.parametrize(
    "busy",
    [
        "missing",
        "never-started",
        "analysis-queued",
        "analysis-running",
        "enrichment-queued",
        "enrichment-running",
    ],
)
def test_preparation_avoids_catalog_ranking_for_missing_or_active_work(monkeypatch, busy):
    article, facts = scenario()
    if busy == "never-started":
        facts.update(analysis=None, enrichment=None)
    elif "-" in busy:
        target, status = busy.split("-")
        facts[target] = active_job(article, facts, target, status=status)
    read = Mock()
    read.scalar.side_effect = [
        None if busy == "missing" else article,
        facts["analysis"],
        facts["enrichment"],
    ]
    factory = factory_for(read, Mock())
    catalog, rank = Mock(), Mock()
    monkeypatch.setattr(automation, "catalog", catalog)
    monkeypatch.setattr(analysis, "analysis_candidates", rank)
    assert automation._prepare_article_candidates(factory, article.id) is None
    catalog.assert_not_called()
    rank.assert_not_called()
    read.get.assert_not_called()


def test_candidate_ranking_happens_after_releasing_the_read_transaction(monkeypatch):
    article, facts = scenario()
    read = Mock()
    read.scalar.side_effect = [article, facts["analysis"], facts["enrichment"]]
    content = ArticleContent(
        article_id=article.id,
        text="Publisher's full Angular routing explanation.",
        method="readability",
    )
    read.get.return_value = content
    factory = factory_for(read, Mock())
    active = {"transaction": True}
    factory.return_value.__exit__.side_effect = lambda *_: active.update(transaction=False)
    monkeypatch.setattr(automation, "catalog", lambda _: facts["taxonomy"])

    def rank(taxonomy, snapshot):
        assert not active["transaction"]
        assert taxonomy is facts["taxonomy"]
        assert snapshot["text"] == content.text

    ranking = Mock(side_effect=rank)
    monkeypatch.setattr(analysis, "analysis_candidates", ranking)
    prepared = automation._prepare_article_candidates(factory, article.id)
    assert prepared == (facts["taxonomy"], automation.source_snapshot(article, content))
    ranking.assert_called_once()
    factory.begin.assert_not_called()


@pytest.mark.parametrize("changed", ["missing", "reviewed", "published", "unchanged"])
def test_paywall_recovery_rechecks_current_editorial_state_before_applying_policy(
    monkeypatch, changed
):
    article, facts = scenario()
    article.review_status = "pending"
    article.origins[0].source.enabled = False
    facts["enrichment"].result["paywalled"] = True
    if changed == "reviewed":
        article.review_status = "approved"
    elif changed == "published":
        article.review_status, article.publication_status = "approved", "published"
        article.published_to_feed_at = facts["now"]
    read, write = Mock(), Mock()
    read.scalars.return_value.all.return_value = [article.id]
    write.scalar.return_value = None if changed == "missing" else article
    factory = factory_for(read, write)
    monkeypatch.setattr(
        automation, "get_settings", lambda: configuration(ai_enabled=False, full_automation=False)
    )
    authority, apply = Mock(), Mock(return_value={"status": "rejected"})
    monkeypatch.setattr(automation, "approved_sources", authority)
    monkeypatch.setattr(automation, "apply_publication_policy", apply)
    assert automation.reject_known_paywalled_articles(factory) == (
        1 if changed == "unchanged" else 0
    )
    authority.assert_called_once_with(write, article.id, lock=True)
    if changed == "unchanged":
        apply.assert_called_once_with(write, article, None)
    else:
        apply.assert_not_called()
