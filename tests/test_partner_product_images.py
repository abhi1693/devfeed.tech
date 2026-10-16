"""Product featured images reuse managed article storage without reader-side upstream I/O."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import image_storage_tasks, image_tasks, product_image_storage
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.image_jobs import retry_image
from devfeed_core.logos import request_logo
from devfeed_core.models import ArticleImageJob, PartnerProduct
from devfeed_core.object_storage import R2Storage
from devfeed_core.partner_product_images import (
    backfill_product_images,
    product_image_url,
    product_image_variants,
    request_product_image,
)
from sqlalchemy import func, select
from test_partner_placements_integration import catalog as catalog
from test_partner_placements_integration import deliver, query

PAGE = "https://official.example/tool"
IMAGE = "https://official.example/preview.png"


@pytest.mark.parametrize(
    "markup,expected",
    [
        ('<meta property="og:image" content="/preview.png">', IMAGE),
        ('<meta name="twitter:image" content="/preview.png">', IMAGE),
        (
            '<meta property="og:image" content="https://cdn.example/card.png">',
            "https://cdn.example/card.png",
        ),
    ],
)
def test_product_preview_discovery_reuses_article_metadata_and_storage(
    monkeypatch, markup, expected
):
    monkeypatch.setattr(
        product_image_storage,
        "fetch_page",
        lambda source: FetchResult(200, markup.encode(), PAGE, content_type="text/html"),
    )
    original = Mock(return_value={"source_url": expected, "variants": [], "hash": "hash"})
    monkeypatch.setattr(
        product_image_storage.ImageOriginalStore,
        "store",
        lambda self, *args: original(self.storage, *args),
    )
    result = product_image_storage.ProductImageOriginalStore(None).store(PAGE)
    original.assert_called_once_with(None, expected)
    assert result["page_url"] == PAGE
    assert result["source_url"] == expected
    assert result["discovery_method"] in {"og:image", "twitter:image"}


@pytest.mark.parametrize(
    "markup",
    [
        '<img src="/random.png">',
        '<meta property="og:image" content="http://127.0.0.1/secret.png">',
        '<meta property="og:image" content="data:image/png,abc">',
        '<template><meta property="og:image" content="/hidden.png"></template>',
    ],
)
def test_missing_or_unsafe_preview_never_fetches_an_arbitrary_image(monkeypatch, markup):
    monkeypatch.setattr(
        product_image_storage,
        "fetch_page",
        lambda source: FetchResult(200, markup.encode(), PAGE, content_type="text/html"),
    )
    original = Mock()
    monkeypatch.setattr(
        product_image_storage.ImageOriginalStore,
        "store",
        lambda self, *args: original(self.storage, *args),
    )
    with pytest.raises(FeedError) as raised:
        product_image_storage.ProductImageOriginalStore(None).store(PAGE)
    assert raised.value.reason == "product_image_not_found"
    original.assert_not_called()


def test_product_reader_image_metadata_is_managed_and_fenced_by_current_website(monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.example")
    get_settings.cache_clear()
    product = PartnerProduct(product_url=PAGE, managed_image={})
    assert product_image_url(product) is None
    product.managed_image = {
        "page_url": PAGE,
        "source_url": "https://remote.example/private-source.png",
        "version": "v1",
        "original_key": "originals/hash",
        "variants": [],
    }
    assert product_image_url(product) == "https://images.example/originals/hash"
    product.managed_image["variants"] = [
        {"key": f"thumbnails/v2/hash/{size}.webp", "width": size} for size in (320, 640, 960)
    ]
    assert product_image_url(product) == "https://images.example/thumbnails/v2/hash/640.webp"
    assert [item["width"] for item in product_image_variants(product)] == [320, 640, 960]
    product.product_url = "https://replacement.example"
    assert product_image_url(product) is None and product_image_variants(product) == []
    product.product_url = PAGE
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "false")
    get_settings.cache_clear()
    assert product_image_url(product) is None and product_image_variants(product) == []


@pytest.fixture
def image_catalog(database, catalog, monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.example")
    get_settings.cache_clear()
    monkeypatch.setattr(
        image_storage_tasks,
        "storage_client",
        lambda: R2Storage("test-images", client=SimpleNamespace(close=lambda: None)),
    )
    original = Mock(
        side_effect=lambda client, source: {
            "page_url": source,
            "source_url": IMAGE,
            "hash": "product-image-hash",
            "original_key": "originals/product-image-hash",
            "source_width": 1200,
            "version": "v1",
            "thumbnail_version": "v2",
            "variants": [],
        }
    )
    variant = Mock(
        side_effect=lambda client, asset, size: {
            "key": f"thumbnails/v2/product-image-hash/{size}.webp",
            "width": size,
        }
    )
    monkeypatch.setattr(
        product_image_storage.ProductImageOriginalStore,
        "store",
        lambda self, *args: original(self.storage, *args),
    )
    monkeypatch.setattr(
        image_storage_tasks.ThumbnailVariantStore,
        "store",
        lambda self, *args: variant(self.storage, *args),
    )
    return catalog, original, variant


def image_job(database, product_id):
    with database.begin() as session:
        return request_product_image(session, product_id).id


@pytest.mark.integration
def test_product_image_and_logo_jobs_coexist_and_featured_image_reaches_public_delivery(
    database, image_catalog
):
    ids, original, variant = image_catalog
    identifier = ids["free_product"]
    job_id = image_job(database, identifier)
    assert image_job(database, identifier) == job_id
    with database.begin() as session:
        logo = request_logo(session, PartnerProduct, identifier)
        assert logo.id != job_id
    image_tasks.enrich_image(str(job_id))
    original.assert_called_once()
    assert [call.args[2] for call in variant.call_args_list] == [320, 640, 960]
    with database.begin() as session:
        assert session.get(ArticleImageJob, job_id).status == "succeeded"
        assert request_product_image(session, identifier, automatic=True) is None
    placement = next(
        item for item in deliver(database, query(ids)) if item["product_id"] == identifier
    )
    assert (
        placement["image_url"] == "https://images.example/thumbnails/v2/product-image-hash/640.webp"
    )
    assert [item["width"] for item in placement["image_variants"]] == [320, 640, 960]


@pytest.mark.integration
def test_no_product_og_image_completes_once_and_keeps_logo_fallback(
    database, image_catalog, monkeypatch
):
    ids, _, _ = image_catalog
    monkeypatch.setattr(
        product_image_storage.ProductImageOriginalStore,
        "store",
        Mock(side_effect=FeedError("No preview", reason="product_image_not_found")),
    )
    identifier = ids["free_product"]
    job_id = image_job(database, identifier)
    image_tasks.enrich_image(str(job_id))
    with database.begin() as session:
        job = session.get(ArticleImageJob, job_id)
        assert job.status == "succeeded" and job.outcome == "not_found"
        assert product_image_url(session.get(PartnerProduct, identifier)) is None
        assert request_product_image(session, identifier, automatic=True) is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(ArticleImageJob)
                .where(ArticleImageJob.product_id == identifier)
            )
            == 1
        )


@pytest.mark.integration
def test_product_image_retry_resumes_checkpointed_sizes_without_refetch(
    database, image_catalog, monkeypatch
):
    ids, original, _ = image_catalog
    sizes = []

    def variant(client, asset, size):
        sizes.append(size)
        if size == 640 and sizes.count(size) == 1:
            raise FeedError("Transform failed", reason="image_transform")
        return {"key": f"thumbnails/v2/product-image-hash/{size}.webp", "width": size}

    monkeypatch.setattr(image_storage_tasks.ThumbnailVariantStore, "store", variant)
    job_id = image_job(database, ids["free_product"])
    image_tasks.enrich_image(str(job_id))
    with database.begin() as session:
        assert session.get(ArticleImageJob, job_id).status == "failed"
        retry = retry_image(session, job_id)
        retry_id = retry.id
        assert [item["width"] for item in retry.storage["variants"]] == [320]
    image_tasks.enrich_image(str(retry_id))
    assert original.call_count == 1 and sizes == [320, 640, 640, 960]


@pytest.mark.integration
def test_product_website_changes_during_image_io_fence_old_assets_and_queue_replacement(
    database, image_catalog, monkeypatch
):
    ids, original, _ = image_catalog
    identifier = ids["free_product"]

    def changed(client, source):
        asset = original(client.storage, source)
        with database.begin() as session:
            session.get(PartnerProduct, identifier).product_url = "https://replacement.example"
        return asset

    monkeypatch.setattr(product_image_storage.ProductImageOriginalStore, "store", changed)
    job_id = image_job(database, identifier)
    image_tasks.enrich_image(str(job_id))
    with database() as session:
        assert session.get(ArticleImageJob, job_id).outcome == "already_present"
        assert product_image_url(session.get(PartnerProduct, identifier)) is None
        replacement = session.scalar(
            select(ArticleImageJob).where(
                ArticleImageJob.product_id == identifier, ArticleImageJob.status == "queued"
            )
        )
        assert replacement.image_url == "https://replacement.example"


@pytest.mark.integration
def test_explicit_backfill_only_queues_approved_active_products_and_is_repeatable(
    database, image_catalog
):
    ids, _, _ = image_catalog
    with database.begin() as session:
        session.get(PartnerProduct, ids["free_product"]).status = "pending"
        jobs = backfill_product_images(session, limit=500)
        assert jobs and ids["free_product"] not in {job.product_id for job in jobs}
        assert backfill_product_images(session, limit=500) == []
