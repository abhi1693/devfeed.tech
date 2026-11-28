"""Pipeline visibility and recovery use the publication admission contract."""

import uuid
from datetime import timedelta

import pytest
from devfeed_core.analysis import (
    PROMPT_VERSION,
    analysis_candidates,
    catalog,
    snapshot_hash,
    source_snapshot,
)
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleOrigin,
    ArticleReview,
    ArticleTopic,
    Source,
    Topic,
    utcnow,
)
from sqlalchemy import func, select

from tests.admin.auth.test_auth import oidc_app as oidc_app


def make_article(session, *, state="ready", source=None, age=0, title="Python task tracing"):
    source = source or Source(
        name="Python publisher",
        source_type="publisher",
        feed_url=f"https://publisher.example/{uuid.uuid4()}/feed",
        approval_status="approved",
        enabled=True,
    )
    topic = session.scalar(select(Topic).where(Topic.name == "Python runtime"))
    if topic is None:
        topic = Topic(
            name="Python runtime",
            slug="python-runtime",
            kind="language",
            aliases=["Python"],
            status="active",
        )
        session.add(topic)
        session.flush()
    article = Article(
        canonical_url=f"https://publisher.example/posts/{uuid.uuid4()}",
        url_hash=uuid.uuid4().hex,
        title=title,
        summary="Trace Python tasks and inspect their runtime state.",
        ai_summary="Inspect task state to diagnose a blocked Python coroutine.",
        ai_description="Trace Python tasks and inspect blocked coroutines.",
        review_status="approved",
        publication_status="unpublished",
        language="en",
        classification_provenance={"developer_relevance": "relevant", "page_kind": "article"},
        discovered_at=utcnow() - timedelta(days=age),
    )
    article.origins = [
        ArticleOrigin(source=source, entry_key=uuid.uuid4().hex, original_url=article.canonical_url)
    ]
    article.topic_links = [
        ArticleTopic(topic=topic, role="primary", relevance=1, evidence="Python", origin="manual")
    ]
    session.add(article)
    session.flush()
    if state == "ingested":
        return article
    enrichment = ArticleEnrichmentJob(
        article_id=article.id,
        status="succeeded",
        outcome="enriched",
        result={"input_url": article.canonical_url, "image_url": None},
        finished_at=utcnow(),
    )
    session.add(enrichment)
    if state == "enrichment":
        enrichment.status = "running"
        return article
    if state == "failed_enrichment":
        enrichment.status = "failed"
        enrichment.error = "Publisher temporarily unavailable"
        return article
    snapshot = source_snapshot(article, None)
    candidates = analysis_candidates(catalog(session), snapshot)
    analysis = ArticleAnalysisJob(
        article_id=article.id,
        status="succeeded",
        outcome="applied",
        input_snapshot=snapshot,
        input_hash=snapshot_hash(snapshot),
        catalog_snapshot=candidates,
        catalog_hash=snapshot_hash(candidates),
        editorial_revision=article.editorial_revision,
        prompt_version=PROMPT_VERSION,
        result={"outcome": "ready", "developer_relevance": "relevant", "page_kind": "article"},
        finished_at=utcnow(),
    )
    session.add(analysis)
    session.flush()
    article.classification_provenance = {
        **article.classification_provenance,
        "analysis_id": str(analysis.id),
        "input_hash": analysis.input_hash,
    }
    if state == "analysis":
        analysis.status = "running"
    elif state == "images":
        article.image_url = "https://publisher.example/image.png"
        session.add(ArticleImageJob(article_id=article.id, status="running", operation="discover"))
    elif state == "review":
        article.review_status = "pending"
    elif state == "published":
        article.publication_status = "published"
        article.published_to_feed_at = utcnow() - timedelta(days=age)
    elif state == "rejected":
        article.review_status = "rejected"
        session.add(
            ArticleReview(
                article_id=article.id,
                action="reject",
                revision=0,
                created_at=utcnow(),
                note="Out of scope",
            )
        )
    return article


def test_source_grouping_requires_admin_before_database_access(oidc_app):
    for path in (
        "/v1/admin/article-pipeline?group_by=source",
        "/v1/admin/article-pipeline/enrichment?group_by=source",
        "/v1/admin/article-pipeline/enrichment?source_group=unknown",
    ):
        assert oidc_app.client.get(path).status_code == 401


@pytest.mark.integration
def test_detail_fallback_and_terminal_publish_actions(admin_client, database):
    with database.begin() as session:
        identifiers = {
            state: str(make_article(session, state=state).id)
            for state in ("ready", "rejected", "published", "ingested")
        }
    ready = admin_client.get(f"/v1/admin/articles/{identifiers['ready']}/pipeline").json()
    assert ready["can_publish"] is True
    assert all(check["status"] == "passed" for check in ready["checks"])
    assert {"overview", "description", "primary_topic", "approval"} <= {
        check["code"] for check in ready["checks"]
    }
    assert (
        next(check for check in ready["checks"] if check["code"] == "images")["status"] == "passed"
    )
    for stage in ("rejected", "published"):
        detail = admin_client.get(f"/v1/admin/articles/{identifiers[stage]}/pipeline").json()
        assert detail["can_publish"] is False
        assert detail["allowed_actions"] == []
    ingested = admin_client.get(f"/v1/admin/articles/{identifiers['ingested']}/pipeline").json()
    assert ingested["can_publish"] is False
    assert ingested["allowed_actions"] == ["advance"]


@pytest.mark.integration
def test_resume_revision_guard_and_terminal_protection(admin_client, database):
    with database.begin() as session:
        article = make_article(session, state="ingested")
        identifier = str(article.id)
        published = str(make_article(session, state="published").id)
    path = f"/v1/admin/articles/{identifier}/pipeline/advance"
    assert admin_client.post(path, json={"expected_revision": 99}).status_code == 409
    resumed = admin_client.post(path, json={"expected_revision": 0})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["stage"] == "enrichment"
    assert resumed.json()["jobs"]["enrichment"]["status"] == "queued"
    assert admin_client.post(path, json={"expected_revision": 0}).status_code == 409
    assert (
        admin_client.post(
            f"/v1/admin/articles/{published}/pipeline/advance", json={"expected_revision": 0}
        ).status_code
        == 409
    )
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ArticleEnrichmentJob)
                .where(ArticleEnrichmentJob.article_id == uuid.UUID(identifier))
            )
            == 1
        )


@pytest.mark.integration
def test_failed_waiting_articles_are_visible_and_not_automatically_retried(admin_client, database):
    with database.begin() as session:
        article = make_article(session, state="failed_enrichment", age=40)
        identifier = str(article.id)
    detail = admin_client.get(f"/v1/admin/articles/{identifier}/pipeline").json()
    assert detail["can_publish"] is False
    assert detail["allowed_actions"] == []


@pytest.mark.integration
def test_active_old_run_wins_over_newer_terminal_history(admin_client, database):
    with database.begin() as session:
        article = make_article(session)
        identifier = str(article.id)
        active = ArticleAnalysisJob(
            article_id=article.id, status="running", created_at=utcnow() - timedelta(days=1)
        )
        session.add(active)
        session.flush()
    detail = admin_client.get(f"/v1/admin/articles/{identifier}/pipeline").json()
    assert detail["can_publish"] is False
    assert "analysis_pending" in detail["blockers"]


@pytest.mark.integration
def test_cutoff_changes_invalidate_warm_counts_and_prevent_noop_resume(
    admin_client, database, monkeypatch
):
    from datetime import UTC, datetime

    from devfeed_core.config import get_settings

    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_AI_CONTENT_NOT_BEFORE", "2026-10-01")
    get_settings.cache_clear()
    with database.begin() as session:
        article = make_article(session)
        article.published_at = datetime(2026, 10, 18, tzinfo=UTC)
        identifier = str(article.id)
    with database.begin() as session:
        session.get(Article, uuid.UUID(identifier)).published_at = datetime(2026, 9, 30, tzinfo=UTC)
        no_analysis = make_article(session, state="enrichment")
        no_analysis.published_at = datetime(2026, 9, 30, tzinfo=UTC)
        job = session.scalar(
            select(ArticleEnrichmentJob).where(ArticleEnrichmentJob.article_id == no_analysis.id)
        )
        job.status = "succeeded"
        no_analysis_id = str(no_analysis.id)
    detail = admin_client.get(f"/v1/admin/articles/{no_analysis_id}/pipeline").json()
    assert "content_date_deferred" in detail["blockers"]
    assert detail["allowed_actions"] == []
    assert (
        admin_client.post(
            f"/v1/admin/articles/{no_analysis_id}/pipeline/advance", json={"expected_revision": 0}
        ).status_code
        == 409
    )


@pytest.mark.integration
def test_missing_source_evidence_offers_explicit_refetch_only(admin_client, database):
    with database.begin() as session:
        article = make_article(session, state="enrichment")
        article.summary = ""
        identifier = str(article.id)
        job = session.scalar(
            select(ArticleEnrichmentJob).where(ArticleEnrichmentJob.article_id == article.id)
        )
        job.status = "succeeded"
        job.outcome = "not_found"
    detail = admin_client.get(f"/v1/admin/articles/{identifier}/pipeline").json()
    assert "insufficient_source_text" in detail["blockers"]
    assert detail["advance_label"] == "Fetch source again"
    assert detail["can_publish"] is False
    resumed = admin_client.post(
        f"/v1/admin/articles/{identifier}/pipeline/advance", json={"expected_revision": 0}
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["jobs"]["enrichment"]["status"] == "queued"
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ArticleEnrichmentJob)
                .where(ArticleEnrichmentJob.article_id == uuid.UUID(identifier))
            )
            == 2
        )


@pytest.mark.integration
def test_pipeline_exposes_specialist_progress_without_private_results(admin_client, database):
    from devfeed_core.article_analysis_steps import STEP_SPECS

    with database.begin() as session:
        article = make_article(session, state="analysis")
        identifier = str(article.id)
        job = session.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == article.id)
        )
        job.status = "queued"
        job.attempts = 2
        job.available_at = utcnow() + timedelta(minutes=2)
        job.active_step = "overview"
        records = {}
        for name, spec in STEP_SPECS.items():
            records[name] = {
                "status": "pending"
                if name in {"overview", "description"}
                else "skipped"
                if name == "identifying_title"
                else "succeeded",
                "prompt_version": spec.prompt_version,
                "attempts": 2 if name == "overview" else 0 if name == "identifying_title" else 1,
                "output": {"private": "DO_NOT_EXPOSE_DRAFT_OUTPUT"},
                "input_hash": "PRIVATE_CONTEXT_IDENTITY",
            }
        records["overview"]["error"] = "invalid_analysis_result"
        records["overview"]["available_at"] = job.available_at.isoformat()
        job.steps = records
    response = admin_client.get(f"/v1/admin/articles/{identifier}/pipeline")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert [step["name"] for step in detail["analysis_steps"]] == list(STEP_SPECS)
    overview = next(step for step in detail["analysis_steps"] if step["name"] == "overview")
    assert overview["attempts"] == 2 and overview["available_at"] is not None
    assert overview["error"] == "invalid_analysis_result"
    assert "DO_NOT_EXPOSE_DRAFT_OUTPUT" not in response.text
    assert "PRIVATE_CONTEXT_IDENTITY" not in response.text
    assert detail["can_publish"] is False
