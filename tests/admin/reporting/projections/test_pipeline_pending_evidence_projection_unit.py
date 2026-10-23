"""Pending inference and saved processing facts retain honest operator projections.

Repository facts are preselected boundaries. Actual admission and report models
remain real; SQL eligibility and transaction isolation are separate contracts.
"""

import uuid
from collections import OrderedDict
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_admin_api import article_pipeline, overview_insights, overview_processing
from devfeed_admin_api import pipeline_projection as projections
from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.models import Article, ArticleOrigin, ReadModelState
from devfeed_core.urls import fingerprint

from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import configuration


@pytest.fixture
def awaiting_first_analysis(processing_case):
    previous, completed = processing_case
    # A metadata-only extraction legitimately retains useful RSS evidence without a body row.
    completed["enrichment"].outcome = "metadata_only"
    source = previous.origins[0].source
    article = Article(
        id=previous.id,
        canonical_url=previous.canonical_url,
        url_hash=fingerprint(previous.canonical_url),
        title=previous.title,
        summary=previous.summary,
        ai_title=None,
        ai_summary=None,
        ai_description=None,
        language=None,
        content_type=None,
        content_format=None,
        classification_provenance={},
        review_status="pending",
        publication_status="unpublished",
        editorial_revision=0,
        discovered_at=previous.discovered_at,
        image_url=None,
        managed_image={},
        origins=[
            ArticleOrigin(
                id=uuid.uuid4(),
                article_id=previous.id,
                source=source,
                source_id=source.id,
                entry_key=str(previous.id),
                original_url=previous.canonical_url,
            )
        ],
        topic_links=[],
    )
    facts = {
        "enrichment": {article.id: completed["enrichment"]},
        "analysis": {},
        "content": {},
        "discovery": {},
        "storage": {},
    }
    return article, facts, completed["taxonomy"], completed["now"]


def test_readiness_cache_handles_an_article_not_yet_analyzed_without_hydrating_absent_jobs(
    awaiting_first_analysis, monkeypatch
):
    article, facts, taxonomy, now = awaiting_first_analysis
    monkeypatch.setattr(article_pipeline, "_readiness_cache", OrderedDict())
    monkeypatch.setattr(article_pipeline, "_batch_facts", Mock(return_value=facts))
    session = Mock()
    settings = configuration(ai_enabled=True, image_storage_enabled=False)

    _, first = article_pipeline._batch_readiness(
        session, [article], taxonomy=taxonomy, settings=settings, now=now
    )
    _, repeated = article_pipeline._batch_readiness(
        session, [article], taxonomy=taxonomy, settings=settings, now=now
    )

    assert first[article.id].stage == "analysis"
    assert first[article.id].checks["analysis"].status == "missing"
    assert "current_analysis_required" in first[article.id].blockers
    assert repeated[article.id] is first[article.id]
    session.scalars.assert_not_called()


def test_enabling_storage_requires_a_missing_rss_image_job_without_granting_readiness(
    awaiting_first_analysis,
):
    from devfeed_core.article_lifecycle import derive_publication_readiness

    article, facts, taxonomy, now = awaiting_first_analysis
    article.image_url = "https://publisher.example/preview.png"
    result = derive_publication_readiness(
        article,
        **{key: rows.get(article.id) for key, rows in facts.items()},
        taxonomy=taxonomy,
        settings=configuration(ai_enabled=True, image_storage_enabled=True),
        now=now,
    )
    assert result.checks["images"].status == "missing"
    assert "image_storage_required" in result.blockers
    assert result.stage != "ready" and article.managed_image == {}


def test_reconciliation_of_a_first_ingestion_requests_automatic_extraction_before_analysis(
    awaiting_first_analysis, monkeypatch
):
    from devfeed_core import analysis, article_jobs, article_lifecycle, image_jobs
    from devfeed_core.models import ArticleEnrichmentJob

    article, facts, taxonomy, now = awaiting_first_analysis
    pending = {key: None for key in facts}
    pending.update(taxonomy=taxonomy, settings=configuration(ai_enabled=True), now=now)
    queued = ArticleEnrichmentJob(
        id=uuid.uuid4(),
        article_id=article.id,
        status="queued",
        attempts=0,
        created_at=now,
        available_at=now,
        result={
            "extraction_version": article_jobs.EXTRACTION_VERSION,
            "input_url": article.canonical_url,
        },
    )
    refreshed = {**pending, "enrichment": queued}
    monkeypatch.setattr(
        article_lifecycle, "_processing_facts", Mock(side_effect=[pending, refreshed])
    )
    extraction, inference, image = Mock(return_value=queued), Mock(), Mock()
    monkeypatch.setattr(article_jobs, "request_article_enrichment", extraction)
    monkeypatch.setattr(analysis, "request_analysis", inference)
    monkeypatch.setattr(image_jobs, "request_image", image)
    session = Mock()

    result = article_lifecycle.reconcile_processing(session, article, taxonomy=taxonomy)

    extraction.assert_called_once_with(session, article.id, automatic=True)
    inference.assert_not_called()
    image.assert_not_called()
    assert result.stage == "enrichment" and result.checks["enrichment"].status == "queued"
    session.flush.assert_called_once_with()


def test_pending_article_projection_evaluates_detached_evidence_without_inventing_an_analysis(
    awaiting_first_analysis, monkeypatch
):
    article, facts, taxonomy, now = awaiting_first_analysis
    session = Mock()
    session.execute.side_effect = [
        None,
        Mock(all=Mock(return_value=[(article, "angular routing")])),
        Mock(all=Mock(return_value=[])),
    ]
    session.scalars.return_value = []
    reading = []

    @contextmanager
    def factory():
        reading.append(True)
        try:
            yield session
        finally:
            reading.pop()

    monkeypatch.setattr(projections, "catalog", lambda _: taxonomy)
    monkeypatch.setattr(article_pipeline, "_batch_facts", Mock(return_value=facts))
    monkeypatch.setattr(
        projections,
        "get_settings",
        lambda: configuration(ai_enabled=True, image_storage_enabled=False),
    )
    from devfeed_admin_api import automation

    monkeypatch.setattr(automation, "automation_blocker_rows", lambda *_: {})
    derive = projections.derive_publication_readiness

    def detached(*args, **kwargs):
        assert not reading
        session.expunge_all.assert_called_once_with()
        return derive(*args, **kwargs)

    monkeypatch.setattr(projections, "derive_publication_readiness", detached)
    [row] = projections.article_rows(factory, [article.id], "policy", now)

    assert row["entity_id"] == article.id and row["stage"] == "analysis"
    assert row["terminal_at"] is None and row["has_active_work"] is False
    assert article.publication_status == "unpublished"
    assert "current_analysis_required" in row["payload"]["blockers"]
    # Only the batched content read is requested; no analysis identity exists to hydrate.
    session.scalars.assert_called_once()


@pytest.mark.parametrize("active", [False, True])
def test_saved_processing_facts_use_typed_projection_without_recomputing_query_aggregates(
    awaiting_first_analysis, active
):
    _, _, _, now = awaiting_first_analysis
    start = now - timedelta(days=1)
    oldest = now - timedelta(days=30) if active else None
    payload = {
        "version": overview_processing.VERSION,
        "active": {
            kind: {"queued": 0, "running": 0, "oldest_queued_at": None} for kind in JOB_DEFINITIONS
        },
        "daily": {str(start.date()): {}, str(now.date()): {}},
    }
    if active:
        payload["active"]["analysis"] = {
            "queued": 2,
            "running": 1,
            "oldest_queued_at": oldest.isoformat(),
        }
        payload["daily"][str(now.date())] = {
            "analysis": {"completed": 4, "failed": 1, "duration_ms": 600, "duration_runs": 3}
        }
    stored = ReadModelState(name=overview_processing.STATE_NAME, payload=payload, updated_at=now)
    session = Mock()
    session.get.return_value = stored
    session.execute.side_effect = AssertionError(
        "Saved processing facts must avoid direct aggregation"
    )

    rows = overview_insights.processing(session, start, now)

    assert {row.kind for row in rows} == set(JOB_DEFINITIONS)
    row = next(row for row in rows if row.kind == "analysis")
    assert (row.queued, row.running, row.completed, row.failed) == (
        (2, 1, 4, 1) if active else (0, 0, 0, 0)
    )
    assert row.oldest_queued_at == oldest
    assert (row.duration_ms, row.duration_runs) == ((600, 3) if active else (0, 0))
    session.execute.assert_not_called()
