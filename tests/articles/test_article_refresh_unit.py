"""Maintenance inputs cannot change public identity or transfer a job's owner."""

import uuid
from datetime import UTC, datetime

import pytest
from devfeed_core import article_refresh as refresh
from devfeed_core.article_refresh_types import RefreshDraft
from devfeed_core.models import Article, ArticleImageJob, ArticleRefresh
from devfeed_core.services import OperationConflict

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def draft(**changes):
    return {
        "fields": {"title": "Python task tracing", "summary": "Trace blocked Python tasks."},
        "topics": [],
        "tags": [],
        **changes,
    }


def owner():
    article = Article(
        id=uuid.uuid4(),
        publication_status="published",
        review_status="approved",
        editorial_revision=3,
    )
    candidate = ArticleRefresh(
        id=uuid.uuid4(),
        article_id=article.id,
        target="images",
        status="review",
        base_revision=3,
        base_hash="a" * 64,
        version=4,
        payload=draft(),
        approved_version=4,
    )
    job = ArticleImageJob(
        id=uuid.uuid4(),
        article_id=article.id,
        refresh_id=candidate.id,
        operation="discover",
        status="queued",
    )
    candidate.discovery_job_id = job.id
    return article, candidate, job


@pytest.mark.parametrize(
    "key",
    [
        "id",
        "slug",
        "feed_at",
        "published_to_feed_at",
        "publication_status",
        "review_status",
        "managed_image",
        "classification_provenance",
    ],
)
@pytest.mark.parametrize("location", ["root", "fields"])
def test_private_inputs_cannot_override_public_identity_or_authority(key, location):
    payload = draft()
    container = payload if location == "root" else payload["fields"]
    container[key] = "unauthorized"
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        RefreshDraft.model_validate(payload)


def test_bounded_private_draft_round_trips_unicode_without_public_identifiers():
    payload = draft(
        fields={"title": "Inspect Python 🐍 tasks", "summary": "Review coroutine state."}
    )
    parsed = RefreshDraft.model_validate(payload)
    assert RefreshDraft.model_validate_json(parsed.model_dump_json()) == parsed
    assert parsed.fields.title == payload["fields"]["title"]
    assert (
        not {"id", "slug", "published_to_feed_at", "publication_status"}
        & parsed.model_dump().keys()
    )


def test_combined_utf8_budget_bounds_individually_valid_private_fields():
    payload = draft(
        fields={"title": "Python", "summary": "🐍" * 60_000},
        topics=[
            {
                "topic_id": str(uuid.uuid4()),
                "role": "primary",
                "relevance": 1,
                "evidence": "🐍" * 2000,
                "origin": "ai",
            }
            for _ in range(100)
        ],
    )
    payload["content"] = {
        "text": "🐍" * 60_000,
        "content_hash": "a" * 64,
        "url": "https://publisher.example/article",
        "method": "readability",
        "retrieved_at": NOW,
    }
    with pytest.raises(ValueError, match="Maintenance inputs exceed"):
        RefreshDraft.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    [
        "owner_status",
        "article_status",
        "article_review",
        "revision",
        "owner_article",
        "job_article",
        "job_owner",
        "selected_job",
    ],
)
def test_current_job_requires_every_private_owner_and_public_authority_guard(change):
    article, candidate, job = owner()
    assert refresh.refresh_job_current(candidate, article, job, "discovery")
    if change == "owner_status":
        candidate.status = "superseded"
    elif change == "article_status":
        article.publication_status = "unpublished"
    elif change == "article_review":
        article.review_status = "rejected"
    elif change == "revision":
        article.editorial_revision += 1
    elif change == "owner_article":
        candidate.article_id = uuid.uuid4()
    elif change == "job_article":
        job.article_id = uuid.uuid4()
    elif change == "job_owner":
        job.refresh_id = uuid.uuid4()
    else:
        candidate.discovery_job_id = uuid.uuid4()
    assert not refresh.refresh_job_current(candidate, article, job, "discovery")


def test_new_work_invalidates_review_consent_and_preserves_explicit_job_binding(monkeypatch):
    _, candidate, job = owner()
    monkeypatch.setattr(refresh, "utcnow", lambda: NOW)
    refresh.select_refresh_job(candidate, "discovery", job)
    assert candidate.version == 5 and candidate.status == "active"
    assert candidate.approved_version is None and candidate.next_check_at == NOW
    assert candidate.discovery_job_id == job.id and job.refresh_id == candidate.id


@pytest.mark.parametrize("change", ["unbound", "other_owner", "other_article", "missing_id"])
def test_job_selection_never_reparents_approved_or_other_private_work(change):
    _, candidate, job = owner()
    if change == "unbound":
        job.refresh_id = None
    elif change == "other_owner":
        job.refresh_id = uuid.uuid4()
    elif change == "other_article":
        job.article_id = uuid.uuid4()
    else:
        job.id = None
    original = job.refresh_id
    with pytest.raises(OperationConflict):
        refresh.select_refresh_job(candidate, "discovery", job)
    assert job.refresh_id == original and candidate.version == 4


@pytest.mark.parametrize("status", ["promoted", "superseded", "cancelled"])
def test_terminal_owner_cannot_be_reactivated_by_a_late_progress_update(status):
    _, candidate, _ = owner()
    candidate.status = status
    with pytest.raises(OperationConflict):
        refresh.touch_refresh(candidate)
    assert candidate.status == status and candidate.version == 4
