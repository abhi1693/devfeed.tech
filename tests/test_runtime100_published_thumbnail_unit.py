"""Published thumbnail upgrades reuse a current original without reopening editorial work.

Receipt negatives validate capability boundaries; they do not claim that normal
admission emits corrupted or stale receipts. PostgreSQL owns the actual emitter,
claim and checkpoint integration regression in the companion integration file.
"""

import copy
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from devfeed_aggregator import image_storage_tasks
from devfeed_core import image_jobs, managed_images
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.managed_images import ARTICLE_THUMBNAIL_LAYOUT, THUMBNAIL_VERSION
from devfeed_core.models import ArticleImageJob
from devfeed_core.urls import fingerprint
from test_analysis import result as analysis_result
from test_processing_retry_eligibility_unit import scenario
from test_runtime_image_commands_unit import asset, claim_session
from test_source_readiness_unit import NOW, configuration


def published(monkeypatch, version):
    with patch("test_article_lifecycle.utcnow", lambda: NOW - timedelta(minutes=1)):
        article, facts = scenario()
    for name in ("analysis", "enrichment"):
        facts[name].available_at = facts[name].created_at
    facts["analysis"].result = analysis_result(
        ai_title=article.ai_title,
        ai_summary=article.ai_summary,
        ai_description=article.ai_description,
        language=article.language,
        topics=[
            {
                "topic_id": article.topic_links[0].topic_id,
                "role": "primary",
                "relevance": 0.9,
                "evidence": "Angular routing",
            }
        ],
    ).model_dump(mode="json")
    article.topic_links[0].origin = "ai"
    settings = configuration(image_storage_enabled=True, image_public_url="https://images.example")
    for module in (image_jobs, managed_images):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(image_jobs, "utcnow", lambda: NOW)
    article.url_hash = fingerprint(article.canonical_url)
    article.image_url = article.canonical_url + ".png"
    article.managed_image = asset(article.image_url, version=version)
    facts["settings"] = settings
    assert derive_publication_readiness(article, **facts).stage == "ready"
    article.publication_status = "published"
    article.published_to_feed_at = facts["now"]
    article.editorial_revision += 1
    return article


def maintenance(article, kind):
    return ArticleImageJob(
        id=uuid.uuid4(),
        article_id=article.id,
        operation="store",
        image_url=article.image_url,
        storage_version=THUMBNAIL_VERSION,
        storage={
            **copy.deepcopy(article.managed_image),
            **(
                {"thumbnail_version": THUMBNAIL_VERSION, "variants": []}
                if kind == "recompression"
                else {"thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT}
            ),
        },
        status="queued",
        attempts=0,
        created_at=NOW,
        available_at=NOW,
        lease_token=None,
        lease_until=None,
    )


@pytest.mark.parametrize("kind", ["recompression", "resize"])
def test_published_derivative_upgrade_claims_and_current_checkpoints_keep_the_publication(
    monkeypatch, kind
):
    article = published(monkeypatch, "v1" if kind == "recompression" else THUMBNAIL_VERSION)
    row = maintenance(article, kind)
    before = copy.deepcopy(
        (
            article.managed_image,
            article.publication_status,
            article.published_to_feed_at,
            article.editorial_revision,
            article.classification_provenance,
            article.image_url,
        )
    )
    result = image_jobs.claim_image(claim_session(article, row), row.id)
    assert result == (row, article.image_url)
    assert row.status == "running" and row.attempts == 1 and row.lease_token is not None
    row.storage = {
        **row.storage,
        "variants": [
            *row.storage["variants"],
            {"key": f"thumbnails/v2/{row.storage['hash']}/480.webp", "width": 480},
        ],
    }
    assert image_jobs.thumbnail_maintenance_current(article, row)
    assert image_storage_tasks._current_source(article, row, "image_url", article.image_url)
    assert (
        article.managed_image,
        article.publication_status,
        article.published_to_feed_at,
        article.editorial_revision,
        article.classification_provenance,
        article.image_url,
    ) == before


@pytest.mark.parametrize(
    "invalid", ["ordinary-store", "refresh", "source", "hash", "original_key", "source_width"]
)
def test_only_a_receipt_for_the_current_saved_original_grants_derivative_maintenance_permission(
    monkeypatch, invalid
):
    article = published(monkeypatch, "v1")
    row = maintenance(article, "recompression")
    if invalid == "ordinary-store":
        row.storage_version = None
    elif invalid == "refresh":
        row.storage = {**row.storage, "article_refresh": True}
    elif invalid == "source":
        row.storage = {**row.storage, "source_url": "https://publisher.example/old.png"}
    else:
        row.storage = {
            **row.storage,
            invalid: {
                "hash": "b" * 64,
                "original_key": "originals/another.png",
                "source_width": 1920,
            }[invalid],
        }
    assert not image_jobs.thumbnail_maintenance_current(article, row)
    assert not image_storage_tasks._current_source(article, row, "image_url", article.image_url)


def test_storage_kill_switch_keeps_queued_published_maintenance_available_for_resume(monkeypatch):
    article = published(monkeypatch, "v1")
    row = maintenance(article, "recompression")
    settings = image_jobs.get_settings()
    settings.image_storage_enabled = False
    assert image_jobs.claim_image(claim_session(article, row), row.id) is None
    assert row.status == "queued" and row.attempts == 0 and row.lease_token is None
    settings.image_storage_enabled = True
    assert image_jobs.claim_image(claim_session(article, row), row.id) == (row, article.image_url)
    assert row.status == "running" and row.attempts == 1


def test_completion_authority_uses_the_saved_original_when_presentation_settings_change(
    monkeypatch,
):
    article = published(monkeypatch, "v1")
    row = maintenance(article, "recompression")
    assert image_jobs.claim_image(claim_session(article, row), row.id) is not None
    settings = image_jobs.get_settings()
    settings.image_storage_enabled, settings.image_public_url = False, None
    assert image_jobs.thumbnail_maintenance_current(article, row)
    assert image_storage_tasks._current_source(article, row, "image_url", article.image_url)
