"""Prerequisite admission and topic waits preserve current editorial authority."""

import uuid
from datetime import timedelta
from unittest.mock import Mock

from devfeed_core import (
    analysis,
    article_automation,
    article_lifecycle,
    image_jobs,
    source_relevance,
)
from devfeed_core.models import ArticleImageJob
from test_article_automation_commands_unit import run
from test_article_automation_commands_unit import runtime as runtime
from test_article_readiness_unit import processing_case as processing_case
from test_pipeline_pending_evidence_projection_unit import (
    awaiting_first_analysis as awaiting_first_analysis,
)
from test_source_readiness_unit import configuration


def test_unstored_publisher_image_requests_storage_before_reporting_processing_ready(
    awaiting_first_analysis, monkeypatch
):
    article, batched, taxonomy, now = awaiting_first_analysis
    article.image_url = "https://publisher.example/article-preview.png"
    extracted = batched["enrichment"][article.id]
    extracted.changed_fields = ["image_url"]
    extracted.result.update(image_url=article.image_url, text_source=None, sample_characters=0)
    settings = configuration(
        ai_enabled=False, image_storage_enabled=True, image_public_url="https://images.example"
    )
    facts = {
        **{name: rows.get(article.id) for name, rows in batched.items()},
        "taxonomy": taxonomy,
        "settings": settings,
        "now": now,
    }
    queued = ArticleImageJob(
        id=uuid.uuid4(),
        article_id=article.id,
        operation="store",
        status="queued",
        attempts=0,
        image_url=article.image_url,
        storage={},
        created_at=now,
        available_at=now,
    )
    monkeypatch.setattr(article_lifecycle, "get_settings", lambda: settings)
    monkeypatch.setattr(article_lifecycle, "_processing_facts", lambda *_: facts)

    def request(session, identifier, *, automatic):
        assert identifier == article.id and automatic is True
        facts["storage"] = queued
        return queued

    storage = Mock(side_effect=request)
    monkeypatch.setattr(image_jobs, "request_image", storage)
    session = Mock()
    result = article_lifecycle.reconcile_processing(session, article, taxonomy=taxonomy)
    storage.assert_called_once_with(session, article.id, automatic=True)
    assert result.checks["images"].job_id == queued.id
    assert result.checks["images"].status == "queued"
    assert "image_processing_pending" in result.blockers
    assert article.publication_status == "unpublished" and article.review_status == "pending"


def test_incomplete_current_analysis_waits_for_matching_topic_work_without_rejecting_article(
    runtime,
):
    current = runtime.facts["analysis"]
    parsed = analysis.AnalysisResult(
        outcome="insufficient_evidence",
        developer_relevance="relevant",
        page_kind="article",
        language=None,
        content_type=None,
        content_format=None,
        topics=[],
        tags=[],
        ai_title=None,
        title_evidence=None,
        ai_summary=None,
        ai_description=None,
        reasons=["No supported primary topic is established yet."],
    )
    current.outcome, current.result = "insufficient_evidence", parsed.model_dump(mode="json")
    runtime.article.ai_title = runtime.article.ai_summary = runtime.article.ai_description = None
    runtime.article.classification_provenance, runtime.article.topic_links = {}, []
    runtime.facts["enrichment"].outcome = "metadata_only"
    runtime.article.author = "Publisher engineering team"
    runtime.facts["enrichment"].changed_fields = ["author"]
    runtime.facts["enrichment"].result.update(text_source=None, sample_characters=0)
    runtime.article.automation_started_at = runtime.now - timedelta(hours=1)
    runtime.pending = True
    runtime.evaluate.return_value = {"status": "blocked", "reasons": ["missing_ai_summary"]}
    runtime.policy_status = "blocked"
    counts = run(runtime)
    assert counts["articles_checked"] == 1
    assert counts["articles_published"] == counts["articles_rejected"] == 0
    runtime.topic_match.assert_called_once()
    runtime.apply.assert_not_called()
    runtime.analyze.assert_not_called()
    assert runtime.article.review_status == "pending"


def test_source_admission_passes_the_configured_batch_budget_to_review_scheduling(monkeypatch):
    settings = configuration(automation_batch_size=7)
    monkeypatch.setattr(article_automation, "get_settings", lambda: settings)
    schedule = Mock(return_value=3)
    monkeypatch.setattr(source_relevance, "schedule_pending_reviews", schedule)
    factory = Mock()
    assert article_automation.schedule_source_admission(factory) == 3
    schedule.assert_called_once_with(factory, 7)
