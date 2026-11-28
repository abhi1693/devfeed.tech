"""Image admission, ownership, and published thumbnail maintenance commands."""

import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from devfeed_aggregator import image_storage_tasks
from devfeed_core import image_jobs, logos, managed_images
from devfeed_core import image_jobs as images
from devfeed_core import partner_product_images as products
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.jobs import LEASE_SECONDS
from devfeed_core.managed_images import ARTICLE_THUMBNAIL_LAYOUT, IMAGE_VERSION, THUMBNAIL_VERSION
from devfeed_core.models import (
    Article,
    ArticleImageJob,
    PartnerProduct,
    PartnerProductPromotion,
    Source,
)
from devfeed_core.services import OperationConflict
from devfeed_core.urls import fingerprint

from tests.analysis.test_analysis import result as analysis_result
from tests.articles.test_processing_retry_eligibility_unit import scenario
from tests.support.source_readiness import NOW, configuration, source


@pytest.fixture
def settings(monkeypatch):
    value = configuration(image_storage_enabled=True, image_public_url="https://images.example")
    for module in (images, managed_images, logos):
        monkeypatch.setattr(module, "get_settings", lambda: value)
    monkeypatch.setattr(images, "utcnow", lambda: NOW)
    return value


def article(**changes):
    return Article(
        **{
            "id": uuid.uuid4(),
            "canonical_url": "https://publisher.example/article",
            "title": "Image storage",
            "review_status": "pending",
            "publication_status": "unpublished",
            "image_url": "https://publisher.example/preview.png",
            "managed_image": {},
            **changes,
        }
    )


def asset(url, *, version=THUMBNAIL_VERSION, digest="a" * 64):
    return {
        "source_url": url,
        "version": "v1",
        "thumbnail_version": version,
        "hash": digest,
        "original_key": f"originals/{digest}.png",
        "source_width": 960,
        "variants": [{"key": f"thumbnails/{version}/{digest}/960.webp", "width": 960}],
    }


def job(current, **changes):
    return ArticleImageJob(
        **{
            "id": uuid.uuid4(),
            "article_id": current.id if isinstance(current, Article) else None,
            "source_id": current.id if isinstance(current, Source) else None,
            "operation": "store" if isinstance(current, Article) else "source-logo",
            "image_url": current.image_url if isinstance(current, Article) else current.logo_url,
            "storage": {},
            "storage_version": None if isinstance(current, Article) else logos.LOGO_VERSION,
            "status": "queued",
            "attempts": 0,
            "created_at": NOW - timedelta(minutes=2),
            "available_at": NOW - timedelta(minutes=1),
            "lease_token": None,
            "lease_until": None,
            **changes,
        }
    )


def claim_session(current, queued):
    session = Mock()
    # Subject locks are database operations; only the final admitted job is supplied.
    session.scalar.side_effect = [queued] + [None] * 5 + [queued]
    session.get.return_value = current
    return session


@pytest.mark.parametrize("automatic", [False, True])
def test_published_article_requires_private_maintenance_for_new_image_work(settings, automatic):
    from unittest.mock import patch

    with patch("tests.articles.test_article_lifecycle.utcnow", lambda: NOW):
        current, facts = scenario()
    assert derive_publication_readiness(current, **facts).stage == "ready"
    current.publication_status = "published"
    current.published_to_feed_at = facts["now"]
    session = Mock()
    session.scalar.return_value = current
    if automatic:
        assert images.request_image(session, current.id, automatic=True) is None
    else:
        with pytest.raises(OperationConflict, match="article maintenance"):
            images.request_image(session, current.id, refresh=True)
    session.add.assert_not_called()
    assert current.publication_status == "published"


@pytest.mark.parametrize("change", ["disabled", "url", "current"])
def test_storage_claim_rechecks_current_input_and_only_leases_work_that_is_still_needed(
    settings, monkeypatch, change
):
    current = article()
    queued = job(current)
    if change == "disabled":
        settings.image_storage_enabled = False
    elif change == "url":
        current.image_url = "https://publisher.example/replaced.png"
    request = Mock()
    monkeypatch.setattr(images, "request_image", request)
    session = claim_session(current, queued)
    result = images.claim_image(session, queued.id)
    if change == "current":
        assert result == (queued, current.image_url) and queued.status == "running"
        assert queued.attempts == 1 and queued.lease_token is not None
        assert queued.lease_until == NOW + timedelta(seconds=LEASE_SECONDS)
        request.assert_not_called()
    else:
        assert result is None and queued.attempts == 0 and queued.lease_token is None
        if change == "disabled":
            assert queued.status == "queued"
            request.assert_not_called()
        else:
            assert queued.status == "succeeded" and queued.outcome == "already_present"
            request.assert_called_once_with(session, current.id, automatic=True)


@pytest.mark.parametrize("storage", [False, True])
def test_publisher_image_arriving_before_discovery_is_not_replaced_by_the_old_lookup(
    settings, monkeypatch, storage
):
    current = article(image_url=None)
    queued = job(
        current, operation="discover", image_url=None, storage={"input_url": current.canonical_url}
    )
    current.image_url = "https://publisher.example/feed-preview.png"
    settings.image_storage_enabled = storage
    request = Mock()
    monkeypatch.setattr(images, "request_image", request)
    assert images.claim_image(claim_session(current, queued), queued.id) is None
    assert (
        queued.status == "succeeded"
        and queued.outcome == "already_present"
        and queued.attempts == 0
    )
    assert current.image_url.endswith("feed-preview.png")
    assert request.call_count == int(storage)


@pytest.mark.parametrize("change", ["disabled", "url", "version", "current", "refresh"])
def test_source_logo_claim_retires_stale_work_but_an_explicit_refresh_can_replace_a_current_logo(
    settings, monkeypatch, change
):
    current = source(logo_url="https://publisher.example/logo.png")
    queued = job(current)
    if change == "disabled":
        settings.image_storage_enabled = False
    elif change == "url":
        current.logo_url = "https://publisher.example/replacement.png"
    elif change == "version":
        queued.storage_version = "old-logo-format"
    if change == "refresh":
        current.managed_logo = {
            "source_url": current.logo_url,
            "version": logos.LOGO_VERSION,
            "variants": [
                {"key": f"logos/current/{width}.webp", "width": width}
                for width in logos.logo_sizes(Source)
            ],
        }
    if change == "refresh":
        queued.operation = "source-logo-refresh"
    request = Mock()
    monkeypatch.setattr(logos, "request_logo", request)
    session = claim_session(current, queued)
    result = images.claim_image(session, queued.id)
    if change in {"current", "refresh"}:
        assert result == (queued, current.logo_url) and queued.status == "running"
        assert queued.attempts == 1 and queued.lease_until == NOW + timedelta(seconds=LEASE_SECONDS)
        request.assert_not_called()
    else:
        assert result is None and queued.attempts == 0
        if change == "disabled":
            assert queued.status == "queued"
            request.assert_not_called()
        else:
            assert queued.status == "succeeded" and queued.outcome == "already_present"
            request.assert_called_once_with(session, Source, current.id, automatic=True)


@pytest.mark.parametrize("operation", ["recompress", "resize"])
@pytest.mark.parametrize("active", [False, True])
def test_thumbnail_maintenance_preserves_the_served_asset_and_reuses_active_work(
    settings, operation, active
):
    current = article()
    current.managed_image = asset(
        current.image_url, version="v1" if operation == "recompress" else THUMBNAIL_VERSION
    )
    before = copy.deepcopy(current.managed_image)
    running = job(
        current,
        storage_version=THUMBNAIL_VERSION,
        storage={
            **before,
            **(
                {"thumbnail_version": THUMBNAIL_VERSION, "variants": []}
                if operation == "recompress"
                else {"thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT}
            ),
        },
        status="running",
        attempts=1,
        lease_token=uuid.uuid4(),
        lease_until=NOW + timedelta(minutes=4),
    )
    session = Mock()
    session.scalar.side_effect = [current, running if active else None]
    request = (
        images.request_thumbnail_recompression
        if operation == "recompress"
        else images.request_thumbnail_resize
    )
    chosen = request(session, current.id)
    assert current.managed_image == before
    if active:
        assert chosen is running and chosen.status == "running" and chosen.attempts == 1
        session.add.assert_not_called()
    else:
        assert chosen.article_id == current.id and chosen.image_url == current.image_url
        assert chosen.operation == "store" and chosen.storage_version == THUMBNAIL_VERSION
        assert chosen.storage["original_key"] == before["original_key"]
        if operation == "recompress":
            assert (
                chosen.storage["variants"] == []
                and chosen.storage["thumbnail_version"] == THUMBNAIL_VERSION
            )
        else:
            assert chosen.storage["thumbnail_layout"] == ARTICLE_THUMBNAIL_LAYOUT
        session.add.assert_called_once_with(chosen)


@pytest.mark.parametrize("change", ["current", "url", "hash"])
def test_resize_reuses_only_a_checkpoint_from_the_same_original(settings, change):
    current = article()
    current.managed_image = asset(current.image_url)
    checkpoint = {
        **current.managed_image,
        "thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT,
        "variants": current.managed_image["variants"]
        + [{"key": "thumbnails/v2/partial/480.webp", "width": 480}],
    }
    if change == "url":
        checkpoint["source_url"] = "https://publisher.example/old.png"
    elif change == "hash":
        checkpoint["hash"] = "b" * 64
    session = Mock()
    session.scalar.side_effect = [current, None]
    chosen = images.request_thumbnail_resize(session, current.id, checkpoint=checkpoint)
    assert chosen.storage["source_url"] == current.image_url
    assert chosen.storage["hash"] == current.managed_image["hash"]
    assert len(chosen.storage["variants"]) == (2 if change == "current" else 1)
    assert chosen.storage["thumbnail_layout"] == ARTICLE_THUMBNAIL_LAYOUT


@pytest.mark.parametrize("operation", ["recompress", "resize"])
def test_thumbnail_maintenance_is_not_scheduled_without_a_complete_saved_original(
    settings, operation
):
    current = article()
    current.managed_image = asset(
        current.image_url, version="v1" if operation == "recompress" else THUMBNAIL_VERSION
    )
    current.managed_image.pop("original_key")
    session = Mock()
    session.scalar.return_value = current
    request = (
        images.request_thumbnail_recompression
        if operation == "recompress"
        else images.request_thumbnail_resize
    )
    assert request(session, current.id) is None
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "operation", ["source-logo", "source-logo-refresh", "resize", "recompress"]
)
def test_retry_routes_to_the_owning_maintenance_command_without_changing_failed_history(
    settings, monkeypatch, operation
):
    current = (
        source(logo_url="https://publisher.example/logo.png")
        if operation.startswith("source-")
        else article()
    )
    storage = (
        {
            "source_url": current.logo_url,
            "version": logos.LOGO_VERSION,
            "hash": "a" * 64,
            "original_key": "logos/original.png",
            "variants": [{"key": "logos/16.webp", "width": 16}],
        }
        if isinstance(current, Source)
        else asset(current.image_url)
    )
    if operation == "resize":
        storage["thumbnail_layout"] = ARTICLE_THUMBNAIL_LAYOUT
    previous = job(
        current,
        operation=operation if operation.startswith("source-") else "store",
        storage=storage,
        storage_version=THUMBNAIL_VERSION
        if operation in {"resize", "recompress"}
        else logos.LOGO_VERSION,
        status="failed",
        attempts=3,
        error="Object store unavailable",
        finished_at=NOW,
    )
    replacement = job(
        current,
        operation=previous.operation,
        storage_version=previous.storage_version,
        created_at=NOW,
        available_at=NOW,
    )
    session, boundary = Mock(), Mock(return_value=replacement)
    session.get.return_value = previous
    if operation.startswith("source-"):
        monkeypatch.setattr(logos, "request_logo", boundary)
    else:
        monkeypatch.setattr(
            images,
            "request_thumbnail_resize"
            if operation == "resize"
            else "request_thumbnail_recompression",
            boundary,
        )
    chosen = images.retry_image(session, previous.id)
    assert (
        chosen is replacement
        and previous.status == "failed"
        and previous.error == "Object store unavailable"
    )
    if operation.startswith("source-"):
        boundary.assert_called_once_with(
            session, Source, current.id, refresh=operation == "source-logo-refresh"
        )
    elif operation == "resize":
        boundary.assert_called_once_with(session, current.id, checkpoint=previous.storage)
    else:
        boundary.assert_called_once_with(session, current.id)
    assert chosen.storage == previous.storage


def product():
    return PartnerProduct(
        id=uuid.uuid4(),
        name="Developer tool",
        tagline="Build software",
        product_url="https://product.example/",
        status="pending",
        excluded=False,
        merged_into_id=None,
        managed_image={},
        managed_logo={},
    )


def uploaded_asset(account_id, product_id, revision=2):
    prefix = products.ad_image_prefix(account_id, product_id)
    cache_version = uuid.uuid4().hex
    return {
        "upload": True,
        "object_product_id": str(product_id),
        "object_prefix": prefix,
        "cache_version": cache_version,
        "source_url": f"https://images.example/{prefix}/800.webp?v={cache_version}",
        "subject_revision": revision,
        "version": IMAGE_VERSION,
        "variants": [
            {"key": f"{prefix}/{width}.webp", "width": width} for width in products.AD_IMAGE_SIZES
        ],
    }


def queued(current, **changes):
    return ArticleImageJob(
        **{
            "id": uuid.uuid4(),
            "product_id": current.id,
            "operation": "product-image",
            "image_url": current.product_url,
            "storage": {},
            "storage_version": "v2",
            "status": "queued",
            "attempts": 0,
            "created_at": NOW - timedelta(minutes=2),
            "available_at": NOW - timedelta(minutes=1),
            "lease_token": None,
            "lease_until": None,
            **changes,
        }
    )


@pytest.mark.parametrize("change", ["current", "url", "excluded", "merged", "disabled"])
def test_product_preview_claim_rechecks_the_page_and_retains_excluded_or_merged_authority(
    settings, monkeypatch, change
):
    current = product()
    row = queued(current)
    if change == "url":
        current.product_url = "https://product.example/new"
    elif change == "excluded":
        current.excluded = True
        current.status = "paused"
    elif change == "merged":
        current.merged_into_id = uuid.uuid4()
        current.status = "withdrawn"
    elif change == "disabled":
        settings.image_storage_enabled = False
    session = claim_session(current, row)
    request = Mock()
    monkeypatch.setattr(products, "request_product_image", request)
    result = images.claim_image(session, row.id)
    if change == "current":
        assert (
            result == (row, current.product_url) and row.status == "running" and row.attempts == 1
        )
        request.assert_not_called()
    else:
        assert result is None and row.attempts == 0 and row.lease_token is None
        if change == "disabled":
            assert row.status == "queued"
            request.assert_not_called()
        else:
            assert row.status == "succeeded" and row.outcome == "already_present"
            if change == "url":
                request.assert_called_once_with(session, current.id, automatic=True)
            else:
                request.assert_not_called()


@pytest.mark.parametrize("change", ["current", "disabled"])
def test_custom_image_claim_uses_the_current_account_policy_revision(settings, change):
    current = product()
    policy = PartnerProductPromotion(
        account_id=uuid.uuid4(),
        product_id=current.id,
        ad_image_url="https://creative.example/image.png",
        ad_image_revision=2,
        managed_ad_image={},
    )
    row = queued(
        current,
        operation="product-ad-image",
        account_id=policy.account_id,
        subject_revision=2,
        image_url=policy.ad_image_url,
    )
    if change == "disabled":
        settings.image_storage_enabled = False
    session = claim_session(current, row)
    session.get.side_effect = [current, policy]
    result = images.claim_image(session, row.id)
    if change == "current":
        assert (
            result == (row, policy.ad_image_url) and row.status == "running" and row.attempts == 1
        )
    else:
        assert result is None and row.status == "queued" and row.attempts == 0
        assert row.lease_token is None


@pytest.mark.parametrize("change", ["current", "disabled"])
def test_uploaded_image_cleanup_claim_is_fenced_by_the_exact_remaining_upload(settings, change):
    current = product()
    current.merged_into_id = uuid.uuid4()
    current.status = "withdrawn"
    account_id = uuid.uuid4()
    prefix = products.ad_image_prefix(account_id, current.id)
    upload = uploaded_asset(account_id, current.id)
    policy = PartnerProductPromotion(
        account_id=account_id,
        product_id=current.id,
        ad_image_url=upload["source_url"],
        ad_image_revision=2,
        managed_ad_image=upload,
    )
    row = queued(
        current,
        operation="product-ad-image",
        account_id=account_id,
        subject_revision=2,
        image_url=policy.ad_image_url,
        storage={"cleanup_upload": prefix},
    )
    if change == "disabled":
        settings.image_storage_enabled = False
    session = claim_session(current, row)
    session.get.side_effect = [current, policy]
    result = images.claim_image(session, row.id)
    if change == "current":
        assert (
            result == (row, policy.ad_image_url) and row.status == "running" and row.attempts == 1
        )
    else:
        assert result is None and row.status == "queued" and row.attempts == 0


def test_failed_cleanup_retry_reuses_only_a_current_account_policy_and_keeps_the_failed_run(
    settings, monkeypatch
):
    current = product()
    current.merged_into_id = uuid.uuid4()
    current.status = "withdrawn"
    account_id = uuid.uuid4()
    prefix = products.ad_image_prefix(account_id, current.id)
    upload = uploaded_asset(account_id, current.id)
    policy = PartnerProductPromotion(
        account_id=account_id,
        product_id=current.id,
        ad_image_url=upload["source_url"],
        ad_image_revision=2,
        managed_ad_image=upload,
    )
    previous = queued(
        current,
        operation="product-ad-image",
        account_id=account_id,
        subject_revision=2,
        image_url=policy.ad_image_url,
        storage={"cleanup_upload": prefix},
        status="failed",
        attempts=3,
        finished_at=NOW,
        error="Storage unavailable",
    )
    replacement = queued(
        current,
        operation="product-ad-image",
        account_id=account_id,
        subject_revision=2,
        image_url=policy.ad_image_url,
        created_at=NOW,
        available_at=NOW,
    )
    request = Mock(return_value=replacement)
    monkeypatch.setattr(products, "request_ad_image_cleanup", request)
    session = Mock()
    session.get.side_effect = [previous, policy]
    result = images.retry_image(session, previous.id)
    assert result is replacement and replacement.storage == previous.storage
    request.assert_called_once_with(session, policy)
    assert previous.status == "failed" and previous.error == "Storage unavailable"


@pytest.mark.parametrize("maintenance", ["storage", "encoding", "sizes"])
def test_bounded_backfills_count_only_admitted_work_after_the_read_boundary(
    settings, monkeypatch, maintenance
):
    first, second = article(), article()
    first.id, second.id = uuid.UUID(int=1), uuid.UUID(int=2)
    for current in (first, second):
        current.managed_image = (
            {}
            if maintenance == "storage"
            else asset(current.image_url, version="v1" if maintenance == "encoding" else "v2")
        )
    job = ArticleImageJob(
        id=uuid.uuid4(),
        article_id=first.id,
        operation="store",
        image_url=first.image_url,
        storage={},
        storage_version=None if maintenance == "storage" else "v2",
        status="queued",
        attempts=0,
        created_at=NOW,
        available_at=NOW,
    )
    session = Mock()
    session.scalars.return_value.all.return_value = (
        [first, second] if maintenance == "sizes" else [first.id, second.id]
    )
    name = {
        "storage": "request_image",
        "encoding": "request_thumbnail_recompression",
        "sizes": "request_thumbnail_resize",
    }[maintenance]
    request = Mock(side_effect=[job, None])  # A later admission recheck can find no remaining work.
    monkeypatch.setattr(images, name, request)
    execute = {
        "storage": images.backfill_storage,
        "encoding": images.backfill_thumbnail_encoding,
        "sizes": images.backfill_thumbnail_sizes,
    }[maintenance]
    assert execute(session, 3) == [job]
    assert [call.args[1] for call in request.call_args_list] == [first.id, second.id]
    assert all(
        call.kwargs == ({"automatic": True} if maintenance == "storage" else {})
        for call in request.call_args_list
    )


@pytest.mark.parametrize("execute", [images.backfill_storage, images.backfill_thumbnail_sizes])
def test_storage_disabled_backfill_refuses_before_any_article_reads(settings, execute):
    settings.image_storage_enabled = False
    session = Mock()
    with pytest.raises(OperationConflict, match="Enable image storage"):
        execute(session, 3)
    session.scalars.assert_not_called()


def published(monkeypatch, version):
    with patch("tests.articles.test_article_lifecycle.utcnow", lambda: NOW - timedelta(minutes=1)):
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
