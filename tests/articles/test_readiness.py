"""Article readiness and enrichment admission preserve current editorial authority."""

import copy
import uuid
from datetime import timedelta
from itertools import cycle
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import ai_content, analysis, article_jobs, image_jobs
from devfeed_core import article_lifecycle as lifecycle
from devfeed_core.article_lifecycle import (
    derive_publication_readiness,
    processing_readiness,
    publication_readiness,
    reconcile_processing,
)
from devfeed_core.managed_images import IMAGE_VERSION
from devfeed_core.models import ArticleContent
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.urls import fingerprint

from tests.articles.test_processing_retry_eligibility_unit import active_job, scenario
from tests.images.test_commands import published
from tests.support.article_readiness import NOW as NOW
from tests.support.article_readiness import image_job as image_job
from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import configuration


@pytest.mark.parametrize(
    "status,attempts,delay,expected_state,check_status",
    [
        ("queued", 0, 60, "waiting", "queued"),
        ("queued", 1, 60, "retry_wait", "retry_wait"),
        ("queued", 1, -1, "waiting", "queued"),
        ("running", 1, -1, "running", "running"),
        ("failed", 3, -1, "blocked", "failed"),
    ],
)
@pytest.mark.parametrize("step", ["enrichment", "analysis"])
def test_live_work_and_retry_delays_remain_visible_until_completion(
    processing_case, status, attempts, delay, expected_state, check_status, step
):
    article, facts = processing_case
    job = facts[step]
    job.id = uuid.uuid4()  # A fresh run leaves previous applied analysis provenance intact.
    job.status, job.attempts, job.outcome, job.finished_at = status, attempts, None, None
    job.available_at = NOW + timedelta(seconds=delay)
    if status == "running":
        job.lease_token, job.lease_until = uuid.uuid4(), NOW + timedelta(minutes=3)
    if status == "failed":
        job.finished_at, job.error = NOW, "provider_unavailable"

    result = derive_publication_readiness(article, **facts)

    assert (result.stage, result.state) == (step, expected_state)
    assert result.checks[step].status == check_status
    assert result.checks[step].job_id == job.id
    assert result.checks[step].available_at == job.available_at
    assert f"{step}_{'failed' if status == 'failed' else 'pending'}" in result.blockers


@pytest.mark.parametrize("ai_enabled", [False, True])
def test_missing_analysis_never_grants_publication_even_when_ai_is_disabled(
    processing_case, monkeypatch, ai_enabled
):
    article, facts = processing_case
    monkeypatch.setattr(facts["settings"], "ai_enabled", ai_enabled)
    facts["analysis"] = None

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "analysis" and "current_analysis_required" in result.blockers
    assert result.checks["analysis"].status == ("missing" if ai_enabled else "disabled")
    assert ("ai_not_configured" in result.blockers) is not ai_enabled


def test_page_paywall_and_unreadable_text_are_independent_publication_blockers(processing_case):
    article, facts = processing_case
    article.summary = "12345 ---"
    facts["enrichment"].result = {**facts["enrichment"].result, "paywalled": True}

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "enrichment" and result.state == "blocked"
    assert {"paywalled_content", "insufficient_source_text"} <= set(result.blockers)
    assert result.checks["enrichment"].status == "complete"


@pytest.mark.parametrize("outcome", ["metadata_only", "not_found"])
def test_completed_page_lookup_can_use_rss_evidence_and_reader_image_fallback(
    processing_case, outcome
):
    article, facts = processing_case
    facts["enrichment"].outcome = outcome

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "ready" and not result.blockers
    assert result.checks["enrichment"].status == "complete"
    assert result.checks["images"].status == "unavailable"


def test_superseded_extraction_cannot_satisfy_current_page_processing(processing_case):
    article, facts = processing_case
    facts["enrichment"].outcome = "superseded"

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "enrichment" and result.state == "blocked"
    assert result.checks["enrichment"].status == "stale"
    assert "current_enrichment_required" in result.blockers


def test_manual_metadata_preserves_current_generation_identity_without_forcing_reanalysis(
    processing_case,
):
    article, facts = processing_case
    original = article.classification_provenance
    article.classification_provenance = {
        "origin": "manual",
        "developer_relevance": "relevant",
        "page_kind": "article",
        "ai_generation": original,
    }

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "ready" and result.blockers == []
    assert result.checks["analysis"].status == "complete"


def test_changed_relevant_catalog_requires_fresh_analysis(processing_case):
    article, facts = processing_case
    facts["taxonomy"] = copy.deepcopy(facts["taxonomy"])
    facts["taxonomy"]["topics"][0]["name"] = "Angular application routing"

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "analysis" and result.state == "blocked"
    assert result.checks["analysis"].status == "stale"
    assert "current_catalog_required" in result.blockers


@pytest.mark.parametrize("missing", ["approval", "language", "source"])
def test_finished_processing_does_not_override_editorial_or_source_authority(
    processing_case, missing
):
    article, facts = processing_case
    if missing == "approval":
        article.review_status = "pending"
    elif missing == "language":
        article.language = None
    else:
        article.origins[0].source.approval_status = "rejected"

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "review"
    assert result.state == ("waiting" if missing == "approval" else "blocked")
    assert {
        "approval": "not_approved",
        "language": "unknown_language",
        "source": "no_approved_source",
    }[missing] in result.blockers
    assert result.checks["analysis"].status == "complete"


@pytest.mark.parametrize(
    "asset_change",
    [
        "wrong-source",
        "old-version",
        "empty-variants",
        "invalid-variant",
        "missing-key",
        "zero-width",
    ],
)
def test_unusable_stored_image_metadata_requires_storage_again(
    processing_case, monkeypatch, asset_change
):
    article, facts = processing_case
    monkeypatch.setattr(facts["settings"], "image_storage_enabled", True)
    monkeypatch.setattr(facts["settings"], "image_public_url", "https://images.example")
    article.image_url = "https://publisher.example/current.png"
    article.managed_image = {
        "source_url": article.image_url,
        "version": IMAGE_VERSION,
        "variants": [{"key": "current.webp", "width": 640}],
    }
    facts["storage"] = image_job(article)
    if asset_change == "wrong-source":
        article.managed_image["source_url"] = "https://publisher.example/old.png"
    elif asset_change == "old-version":
        article.managed_image["version"] = "retired-transform-version"
    elif asset_change == "empty-variants":
        article.managed_image["variants"] = []
    elif asset_change == "invalid-variant":
        article.managed_image["variants"] = [None]
    elif asset_change == "missing-key":
        article.managed_image["variants"] = [{"width": 640}]
    else:
        article.managed_image["variants"] = [{"key": "current.webp", "width": 0}]

    result = derive_publication_readiness(article, **facts)

    assert result.stage == "images" and result.state == "blocked"
    assert result.checks["images"].status == "stale"
    assert "image_storage_required" in result.blockers


@pytest.mark.parametrize("newer_store", [False, True])
def test_explicit_image_refresh_failure_is_visible_until_a_later_storage_success(
    processing_case, monkeypatch, newer_store
):
    article, facts = processing_case
    monkeypatch.setattr(facts["settings"], "image_storage_enabled", True)
    monkeypatch.setattr(facts["settings"], "image_public_url", "https://images.example")
    article.image_url = "https://publisher.example/current.png"
    article.managed_image = {
        "source_url": article.image_url,
        "version": IMAGE_VERSION,
        "variants": [{"key": "current.webp", "width": 640}],
    }
    facts["storage"] = image_job(
        article,
        created_at=NOW - timedelta(seconds=30) if newer_store else NOW - timedelta(minutes=2),
    )
    refresh = facts["discovery"] = image_job(article, operation="discover", status="failed")
    refresh.storage = {
        "input_url": article.canonical_url,
        "article_refresh": True,
        "previous_image_url": article.image_url,
    }
    refresh.error = "page_fetch_unavailable"

    result = derive_publication_readiness(article, **facts)

    if newer_store:
        assert result.stage == "ready" and not result.blockers
        assert result.checks["images"].job_id == facts["storage"].id
    else:
        assert result.stage == "images" and result.state == "blocked"
        assert result.checks["images"].job_id == refresh.id
        assert "image_discovery_failed" in result.blockers


@pytest.mark.parametrize("terminal", ["published", "rejected"])
def test_terminal_history_needs_no_evidence_queries_or_new_processing(processing_case, terminal):
    article, _ = processing_case
    if terminal == "published":
        article.publication_status = "published"
        article.published_to_feed_at = NOW
    else:
        article.review_status = "rejected"

    # Old completed evidence may have been pruned; a terminal view needs no session.
    for derive in (processing_readiness, publication_readiness, reconcile_processing):
        result = derive(None, article)
        assert (result.stage, result.state) == (terminal, "complete")
        assert result.blockers == [] and result.checks == {}


@pytest.fixture
def runtime(monkeypatch):
    article, facts = scenario()
    settings = configuration(ai_enabled=True, ai_content_not_before=None)
    monkeypatch.setattr(lifecycle, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_content, "get_settings", lambda: settings)
    monkeypatch.setattr(lifecycle, "utcnow", lambda: facts["now"])
    session = Mock()
    session.get.return_value = None
    catalog = Mock(return_value=facts["taxonomy"])
    monkeypatch.setattr(analysis, "catalog", catalog)
    enrich, analyze, image = Mock(), Mock(), Mock()
    monkeypatch.setattr(article_jobs, "request_article_enrichment", enrich)
    monkeypatch.setattr(analysis, "request_analysis", analyze)
    monkeypatch.setattr(image_jobs, "request_image", image)
    return SimpleNamespace(
        article=article,
        facts=facts,
        settings=settings,
        session=session,
        catalog=catalog,
        enrich=enrich,
        analyze=analyze,
        image=image,
    )


def reads(value):
    value.session.scalar.side_effect = cycle(
        [value.facts.get(name) for name in ("enrichment", "analysis", "discovery", "storage")]
    )


def test_readiness_adapters_share_the_supplied_catalog_and_derive_without_mutation(runtime):
    reads(runtime)
    result = lifecycle.publication_readiness(
        runtime.session, runtime.article, taxonomy=runtime.facts["taxonomy"]
    )
    assert result.stage == "ready" and result.blockers == []
    assert result.checks["enrichment"].job_id == runtime.facts["enrichment"].id
    assert result.checks["analysis"].job_id == runtime.facts["analysis"].id
    runtime.session.get.assert_called_once_with(ArticleContent, runtime.article.id)
    runtime.catalog.assert_not_called()
    runtime.session.add.assert_not_called()


def test_readiness_loads_a_catalog_only_when_an_analysis_receipt_needs_it(runtime):
    reads(runtime)
    assert lifecycle.processing_readiness(runtime.session, runtime.article).stage == "ready"
    runtime.catalog.assert_called_once_with(runtime.session)


@pytest.mark.parametrize(
    "wrapper",
    [
        lifecycle.processing_readiness,
        lifecycle.publication_readiness,
        lifecycle.reconcile_processing,
    ],
)
@pytest.mark.parametrize("state", ["published", "rejected"])
def test_terminal_editorial_states_do_not_reopen_or_reread_processing(runtime, wrapper, state):
    if state == "published":
        assert (
            lifecycle.derive_publication_readiness(runtime.article, **runtime.facts).stage
            == "ready"
        )
        runtime.article.publication_status = "published"
        runtime.article.published_to_feed_at = runtime.facts["now"]
    else:
        runtime.article.review_status = "rejected"
    assert wrapper(runtime.session, runtime.article).stage == state
    runtime.session.scalar.assert_not_called()
    runtime.session.get.assert_not_called()
    runtime.enrich.assert_not_called()
    runtime.analyze.assert_not_called()
    runtime.image.assert_not_called()


@pytest.mark.parametrize("page", ["missing", "stale", "failed"])
def test_reconciliation_requests_missing_or_changed_page_evidence_but_retains_terminal_failure(
    runtime, page
):
    runtime.article.review_status = "pending"
    if page == "missing":
        runtime.facts["enrichment"] = runtime.facts["analysis"] = None
        runtime.article.classification_provenance = {}
        runtime.article.ai_summary = runtime.article.ai_description = None
    elif page == "stale":
        runtime.article.canonical_url = "https://publisher.example/corrected"
        runtime.article.url_hash = fingerprint(runtime.article.canonical_url)
    else:
        runtime.facts["enrichment"] = active_job(
            runtime.article, runtime.facts, "enrichment", status="failed"
        )
        runtime.facts["enrichment"].error = "Publisher unavailable"
    reads(runtime)
    result = lifecycle.reconcile_processing(
        runtime.session, runtime.article, taxonomy=runtime.facts["taxonomy"]
    )
    if page == "missing":
        runtime.enrich.assert_called_once_with(runtime.session, runtime.article.id, automatic=True)
        runtime.catalog.assert_not_called()
    elif page == "stale":
        runtime.enrich.assert_called_once_with(runtime.session, runtime.article.id)
    else:
        runtime.enrich.assert_not_called()
    assert result.stage in {"ingested", "enrichment"}
    runtime.analyze.assert_not_called()
    runtime.image.assert_not_called()
    runtime.session.flush.assert_called_once()


@pytest.mark.parametrize("change", ["missing", "source-text", "old-prompt", "manual", "ai-off"])
def test_analysis_admission_respects_changed_evidence_manual_classification_and_the_kill_switch(
    runtime, change
):
    runtime.article.review_status = "pending"
    if change == "missing":
        runtime.facts["analysis"] = None
        runtime.article.classification_provenance = {}
        runtime.article.ai_summary = runtime.article.ai_description = None
    elif change == "source-text":
        runtime.article.summary += " Updated publisher guidance."
    elif change == "old-prompt":
        runtime.facts["analysis"].prompt_version = "article-analysis-v1"
    elif change == "manual":
        runtime.article.classification_provenance = {"origin": "manual", "actor": "editor-123"}
    else:
        runtime.settings.ai_enabled = False
    reads(runtime)
    result = lifecycle.reconcile_processing(
        runtime.session, runtime.article, taxonomy=runtime.facts["taxonomy"]
    )
    if change in {"manual", "ai-off"}:
        runtime.analyze.assert_not_called()
    else:
        runtime.analyze.assert_called_once_with(
            runtime.session,
            runtime.article.id,
            automatic=True,
            force=change != "missing",
            taxonomy=runtime.facts["taxonomy"],
        )
        assert result.stage == "analysis"
    runtime.enrich.assert_not_called()


def test_reconciliation_does_not_requeue_an_unchanged_current_analysis(runtime):
    reads(runtime)
    assert (
        lifecycle.reconcile_processing(
            runtime.session, runtime.article, taxonomy=runtime.facts["taxonomy"]
        ).stage
        == "ready"
    )
    runtime.analyze.assert_not_called()
    runtime.enrich.assert_not_called()
    runtime.image.assert_not_called()


@pytest.mark.parametrize("automatic", [False, True])
def test_published_article_requires_withdrawal_before_new_enrichment(monkeypatch, automatic):
    article = published(monkeypatch, "v2")
    session = Mock()
    session.scalar.return_value = article
    approved = Mock(
        side_effect=AssertionError("Published content must stop before source admission")
    )
    monkeypatch.setattr(article_jobs, "approved_sources", approved)
    if automatic:
        assert article_jobs.request_article_enrichment(session, article.id, automatic=True) is None
    else:
        with pytest.raises(OperationConflict, match="Unpublish the article before requesting"):
            article_jobs.request_article_enrichment(session, article.id)
    approved.assert_not_called()
    session.add.assert_not_called()
    session.flush.assert_not_called()
    assert article.publication_status == "published"


@pytest.mark.parametrize("operation", ["retry_article", "prepare_article_dispatch"])
def test_unknown_enrichment_job_returns_a_domain_not_found_without_creating_replacement(operation):
    session = Mock()
    session.get.return_value = session.scalar.return_value = None
    with pytest.raises(RecordNotFound, match="Article enrichment job not found"):
        getattr(article_jobs, operation)(session, uuid.uuid4())
    session.add.assert_not_called()
    session.flush.assert_not_called()
