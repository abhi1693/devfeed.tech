"""Image subject fencing and bounded maintenance command composition over supplied reads."""

import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import image_jobs as images
from devfeed_core import partner_product_images as products
from devfeed_core.managed_images import IMAGE_VERSION
from devfeed_core.models import ArticleImageJob, PartnerProduct, PartnerProductPromotion
from devfeed_core.services import OperationConflict
from test_runtime_image_commands_unit import article, asset, claim_session
from test_runtime_image_commands_unit import settings as settings
from test_source_readiness_unit import NOW


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
