"""Retry actions respect source authority, prerequisites, and active work."""

import uuid
from datetime import timedelta

import pytest
from devfeed_core.article_lifecycle import derive_processing_readiness, derive_publication_readiness
from devfeed_core.article_processing_retry import (
    current_processing_job,
    processing_retry_targets,
    retry_readiness,
)
from devfeed_core.config import get_settings
from devfeed_core.models import ArticleAnalysisJob, ArticleEnrichmentJob, ArticleImageJob

from tests.articles.test_article_lifecycle import facts as completed_facts


def scenario():
    article, values = completed_facts()
    article.origins[0].source.feed_url = "https://publisher.example/feed"
    article.origins[0].source.enabled = True
    article.origins[0].entry_key = str(article.id)
    article.origins[0].original_url = article.canonical_url
    for name in ("analysis", "enrichment"):
        values[name].created_at = values["now"] - timedelta(minutes=3)
        values[name].finished_at = values["now"] - timedelta(minutes=2)
    values.update(discovery=None, storage=None)
    return article, values


def actions(article, values):
    readiness = derive_processing_readiness(article, **values)
    return processing_retry_targets(article, readiness, values, settings=values["settings"])


def active_job(article, values, target, *, status="queued"):
    model = {
        "enrichment": ArticleEnrichmentJob,
        "analysis": ArticleAnalysisJob,
        "discovery": ArticleImageJob,
        "storage": ArticleImageJob,
    }[target]
    job_values = {
        "id": uuid.uuid4(),
        "article_id": article.id,
        "status": status,
        "attempts": 1,
        "available_at": values["now"] + timedelta(minutes=2)
        if status == "queued"
        else values["now"] - timedelta(seconds=1),
        "created_at": values["now"],
        "lease_token": uuid.uuid4() if status == "running" else None,
        "lease_until": values["now"] + timedelta(minutes=3) if status == "running" else None,
        "finished_at": values["now"] if status == "failed" else None,
    }
    if model is ArticleImageJob:
        job_values.update(
            operation="store" if target == "storage" else "discover",
            image_url=article.image_url if target == "storage" else None,
            storage={"input_url": article.canonical_url} if target == "discovery" else {},
        )
    else:
        job_values["result"] = (
            {"input_url": article.canonical_url} if target == "enrichment" else {}
        )
    return model(**job_values)


def test_completed_processing_allows_independent_retries_without_changing_editorial_state():
    article, values = scenario()
    assert actions(article, values) == ["enrichment", "analysis", "images"]
    assert article.review_status == "approved" and article.publication_status == "unpublished"
    assert article.editorial_revision == 2


@pytest.mark.parametrize("restriction", ["rejected", "unapproved-source", "unsafe-url"])
def test_article_and_source_authority_cannot_be_bypassed_by_retry(restriction):
    article, values = scenario()
    if restriction == "rejected":
        article.review_status = "rejected"
    elif restriction == "unapproved-source":
        article.origins[0].source.approval_status = "pending"
    else:
        article.canonical_url = "http://127.0.0.1/private"
    assert actions(article, values) == []


@pytest.mark.parametrize("target", ["enrichment", "analysis", "discovery", "storage"])
@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_or_delayed_work_keeps_its_lease_and_retry_budget(target, status):
    article, values = scenario()
    if target == "storage":
        article.image_url = "https://publisher.example/current-image.png"
        values["settings"].image_storage_enabled = True
        values["settings"].image_public_url = "https://images.example"
    pending = active_job(article, values, target, status=status)
    before = (
        pending.status,
        pending.attempts,
        pending.available_at,
        pending.lease_token,
        pending.lease_until,
    )
    values[target] = pending
    expected_target = "images" if target in {"discovery", "storage"} else target
    assert expected_target not in actions(article, values)
    assert (
        pending.status,
        pending.attempts,
        pending.available_at,
        pending.lease_token,
        pending.lease_until,
    ) == before
    assert pending.status == status and pending.attempts == 1
    if status == "queued":
        assert pending.available_at > values["now"] and pending.lease_token is None
    else:
        assert pending.available_at < values["now"]
        assert pending.lease_token is not None and pending.lease_until > values["now"]


@pytest.mark.parametrize("blocker", ["paywalled_content", "insufficient_source_text"])
def test_unusable_source_evidence_allows_only_extraction_recovery(blocker):
    article, values = scenario()
    values["enrichment"].result = {
        "input_url": article.canonical_url,
        "paywalled": blocker == "paywalled_content",
    }
    if blocker == "insufficient_source_text":
        article.summary = None
        article.ai_summary = None
    readiness = derive_processing_readiness(article, **values)
    assert blocker in readiness.blockers
    assert processing_retry_targets(article, readiness, values, settings=values["settings"]) == [
        "enrichment"
    ]


@pytest.mark.parametrize("missing", ["ai_summary", "ai_description", "title"])
def test_image_retry_waits_until_analysis_has_produced_required_presentation(missing):
    article, values = scenario()
    if missing == "title":
        article.ai_title = "v1.2.3"
    else:
        setattr(article, missing, None)
    readiness = derive_processing_readiness(article, **values)
    blocker = "unidentifiable_title" if missing == "title" else f"missing_{missing}"
    assert blocker in readiness.blockers
    assert readiness.checks["analysis"].status == "complete"
    assert "images" not in processing_retry_targets(
        article, readiness, values, settings=values["settings"]
    )


def test_ai_disabled_never_offers_an_analysis_retry():
    article, values = scenario()
    values["settings"].ai_enabled = False
    assert "analysis" not in actions(article, values)


def test_content_window_prevents_analysis_and_image_retries(monkeypatch):
    from datetime import date

    article, values = scenario()
    monkeypatch.setattr(get_settings(), "ai_content_not_before", date(2026, 1, 1))
    article.published_at = values["now"].replace(year=2025)
    assert actions(article, values) == ["enrichment"]


def test_retry_preview_for_published_article_does_not_hide_publication_or_change_revision():
    article, values = scenario()
    assert derive_publication_readiness(article, **values).stage == "ready"
    article.publication_status = "published"
    article.published_to_feed_at = values["now"]
    published = derive_publication_readiness(article, **values)
    preview = retry_readiness(article, published, values, settings=values["settings"])
    assert published.stage == "published" and preview.stage == "ready"
    assert processing_retry_targets(article, preview, values, settings=values["settings"]) == [
        "enrichment",
        "analysis",
        "images",
    ]
    assert article.publication_status == "published" and article.editorial_revision == 2


@pytest.mark.parametrize("restriction", ["outside-content-window", "old-analysis-with-ai-off"])
def test_published_retries_require_a_safe_path_back_to_review(monkeypatch, restriction):
    from datetime import date

    article, values = scenario()
    article.publication_status = "published"
    article.published_to_feed_at = values["now"]
    if restriction == "outside-content-window":
        monkeypatch.setattr(get_settings(), "ai_content_not_before", date(2026, 1, 1))
        article.published_at = values["now"].replace(year=2025)
    else:
        values["settings"].ai_enabled = False
        # A deployment can change the prompt contract while an existing article
        # remains published; recovery must still have an available analysis path.
        values["analysis"].prompt_version = "previous-release-contract"
    published = derive_publication_readiness(article, **values)
    preview = retry_readiness(article, published, values, settings=values["settings"])
    assert processing_retry_targets(article, preview, values, settings=values["settings"]) == []


def test_unpublished_retry_preview_returns_the_existing_readiness():
    article, values = scenario()
    readiness = derive_processing_readiness(article, **values)
    assert retry_readiness(article, readiness, values) is readiness


def test_current_image_task_follows_the_actual_readiness_blocker():
    article, values = scenario()
    values["discovery"] = active_job(article, values, "discovery", status="running")
    values["storage"] = active_job(article, values, "storage", status="failed")
    values["storage"].image_url = "https://publisher.example/previous-image.png"
    values["storage"].created_at = values["now"] - timedelta(minutes=10)
    readiness = derive_processing_readiness(article, **values)
    assert readiness.checks["images"].job_id == values["discovery"].id
    assert current_processing_job(values, "images", readiness=readiness) is values["discovery"]
    assert current_processing_job(values, "images") is values["discovery"]
    assert current_processing_job(values, "enrichment") is values["enrichment"]
    assert current_processing_job(values, "analysis") is values["analysis"]


def test_missing_image_history_has_no_current_task():
    _, values = scenario()
    assert current_processing_job(values, "images") is None
