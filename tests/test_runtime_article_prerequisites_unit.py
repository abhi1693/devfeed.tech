"""Lifecycle command policy over current facts, without evaluating SQL selection or locks."""

from itertools import cycle
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import ai_content, analysis, article_jobs, image_jobs
from devfeed_core import article_lifecycle as lifecycle
from devfeed_core.models import ArticleContent
from devfeed_core.urls import fingerprint
from test_processing_retry_eligibility_unit import active_job, scenario
from test_source_readiness_unit import configuration


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
