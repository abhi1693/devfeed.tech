"""Published articles may upgrade derivatives from a saved original without editorial changes."""

import copy
import uuid
from contextlib import nullcontext
from unittest.mock import Mock

import pytest
from devfeed_aggregator import image_storage_tasks, image_tasks
from devfeed_core import image_jobs
from devfeed_core.config import get_settings
from devfeed_core.managed_images import ARTICLE_THUMBNAIL_LAYOUT
from devfeed_core.models import Article, ArticleImageJob
from devfeed_core.urls import fingerprint

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("maintenance", ["recompression", "resize"])
@pytest.mark.parametrize("disable_before_completion", [False, True])
def test_published_thumbnail_maintenance_completes_from_the_saved_original(
    database, publish_for_read_test, monkeypatch, maintenance, disable_before_completion
):
    settings = get_settings()
    monkeypatch.setattr(settings, "image_storage_enabled", True)
    monkeypatch.setattr(settings, "image_public_url", "https://images.example")
    url = "https://publisher.example/" + uuid.uuid4().hex
    image, digest = url + ".png", "a" * 64
    version = "v1" if maintenance == "recompression" else "v2"
    asset = {
        "source_url": image,
        "version": "v1",
        "thumbnail_version": version,
        "hash": digest,
        "original_key": f"originals/{digest}.png",
        "source_width": 960,
        "variants": [
            {"key": f"thumbnails/{version}/{digest}/{width}.webp", "width": width}
            for width in (320, 640, 960)
        ],
    }
    with database.begin() as session:
        article = Article(
            canonical_url=url,
            url_hash=fingerprint(url),
            title="Software API architecture",
            summary="A software engineering guide to building maintainable application APIs.",
            image_url=image,
            managed_image=asset,
            content_type="tutorial",
            content_format="article",
        )
        session.add(article)
        session.flush()
        identifier = article.id
    publish_for_read_test([identifier])
    emit = (
        image_jobs.backfill_thumbnail_encoding
        if maintenance == "recompression"
        else image_jobs.backfill_thumbnail_sizes
    )
    with database.begin() as session:
        article = session.get(Article, identifier)
        assert article.publication_status == "published"
        protected = copy.deepcopy(
            (
                article.image_url,
                article.editorial_revision,
                article.published_to_feed_at,
                article.classification_provenance,
                article.ai_title,
                article.ai_summary,
                article.ai_description,
            )
        )
        jobs = emit(session, 1)
        assert len(jobs) == 1
        job_id = jobs[0].id
        assert article.managed_image == asset
    original = Mock(
        side_effect=AssertionError("Published maintenance must reuse the saved original")
    )
    monkeypatch.setattr(image_storage_tasks.ImageOriginalStore, "store", original)
    monkeypatch.setattr(image_storage_tasks, "storage_client", lambda: nullcontext(object()))
    generated = []

    def variant(_store, checkpoint, width):
        assert checkpoint["original_key"] == asset["original_key"]
        assert checkpoint["hash"] == digest and checkpoint["source_url"] == image
        generated.append(width)
        with database() as session:
            article = session.get(Article, identifier)
            assert article.publication_status == "published" and article.managed_image == asset
        if disable_before_completion:
            settings.image_storage_enabled, settings.image_public_url = False, None
        return {"key": f"thumbnails/v2/{digest}/{width}.webp", "width": width}

    monkeypatch.setattr(image_storage_tasks.ArticleThumbnailVariantStore, "store", variant)
    image_tasks.enrich_image(str(job_id))
    original.assert_not_called()
    assert generated == (
        [320, 480, 640, 768, 960] if maintenance == "recompression" else [480, 768]
    )
    with database.begin() as session:
        article, row = session.get(Article, identifier), session.get(ArticleImageJob, job_id)
        assert row.status == "succeeded" and row.outcome == "found" and row.attempts == 1
        assert article.publication_status == "published"
        assert (
            article.image_url,
            article.editorial_revision,
            article.published_to_feed_at,
            article.classification_provenance,
            article.ai_title,
            article.ai_summary,
            article.ai_description,
        ) == protected
        assert {
            key: article.managed_image[key]
            for key in ("source_url", "hash", "original_key", "source_width")
        } == {key: asset[key] for key in ("source_url", "hash", "original_key", "source_width")}
        assert article.managed_image["thumbnail_version"] == "v2"
        assert article.managed_image["thumbnail_layout"] == ARTICLE_THUMBNAIL_LAYOUT
        assert [value["width"] for value in article.managed_image["variants"]] == [
            320,
            480,
            640,
            768,
            960,
        ]
        settings.image_storage_enabled, settings.image_public_url = True, "https://images.example"
        assert emit(session, 1) == []
