"""Article processing evidence and image job factories."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleImageJob, ArticleOrigin
from devfeed_core.urls import fingerprint

from tests.articles.test_article_lifecycle import facts as completed_facts

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


@pytest.fixture
def processing_case(monkeypatch):
    article, facts = completed_facts()
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    article.discovered_at = NOW - timedelta(hours=1)
    article.automation_next_check_at = NOW + timedelta(minutes=5)
    article.origins[0].source.feed_url = "https://publisher.example/feed"
    article.origins[0].source.enabled = True
    article.origins[0].entry_key = str(article.id)
    article.origins[0].original_url = article.canonical_url
    facts.update(settings=settings, now=NOW, content=None, discovery=None, storage=None)
    for name in ("analysis", "enrichment"):
        job = facts[name]
        job.available_at = job.created_at = NOW - timedelta(minutes=3)
        job.finished_at = NOW - timedelta(minutes=2)
    return article, facts


def image_job(
    article,
    *,
    operation="store",
    status="succeeded",
    created_at=NOW - timedelta(minutes=1),
    **values,
):
    terminal = status in {"succeeded", "failed"}
    details = {
        "finished_at": created_at + timedelta(seconds=1) if terminal else None,
        "outcome": ("stored" if operation == "store" else "not_found")
        if status == "succeeded"
        else None,
        **values,
    }
    return ArticleImageJob(
        id=uuid.uuid4(),
        article_id=article.id,
        operation=operation,
        status=status,
        image_url=article.image_url if operation == "store" else None,
        storage={"input_url": article.canonical_url},
        created_at=created_at,
        available_at=created_at,
        attempts=1,
        **details,
    )


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
