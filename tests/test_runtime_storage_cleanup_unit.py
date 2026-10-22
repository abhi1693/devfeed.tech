"""Object cleanup and storage wrapper protocols over owned job/policy read boundaries."""

import copy
import uuid
from contextlib import nullcontext
from unittest.mock import Mock

import pytest
from devfeed_aggregator import image_storage_tasks as storage
from devfeed_aggregator import product_image_storage
from devfeed_core import partner_product_images as images
from devfeed_core.models import PartnerProduct, PartnerProductPromotion
from test_runtime_image_admission_unit import uploaded_asset
from test_runtime_image_worker_routing_unit import Factory, running
from test_source_readiness_unit import NOW


@pytest.mark.parametrize("result", ["success", "transport-failure", "lease-lost"])
def test_cleanup_keeps_policy_until_the_owned_upload_namespace_is_deleted(monkeypatch, result):
    account_id, product_id = uuid.uuid4(), uuid.uuid4()
    prefix = images.ad_image_prefix(account_id, product_id)
    product = PartnerProduct(
        id=product_id,
        name="Former product identity",
        tagline="Developer tool",
        product_url="https://product.example/",
        status="withdrawn",
        merged_into_id=uuid.uuid4(),
    )
    upload = uploaded_asset(account_id, product_id)
    policy = PartnerProductPromotion(
        account_id=account_id,
        product_id=product_id,
        ad_image_revision=2,
        ad_image_url=upload["source_url"],
        managed_ad_image=upload,
    )
    before = copy.deepcopy(policy.managed_ad_image)
    row = running(
        article_id=None,
        product_id=product_id,
        account_id=account_id,
        subject_revision=2,
        operation="product-ad-image",
        image_url=policy.ad_image_url,
        storage_version="v2",
        storage={"cleanup_upload": prefix},
    )
    session = Mock()
    session.scalar.side_effect = [product_id, policy]
    session.get.return_value = product
    owner = Mock(return_value=None if result == "lease-lost" else row)
    monkeypatch.setattr(storage, "owned_job", owner)
    monkeypatch.setattr(storage, "utcnow", lambda: NOW)
    client, connect = Mock(), Mock()
    connect.return_value = nullcontext(client)
    monkeypatch.setattr(storage, "storage_client", connect)
    if result == "transport-failure":
        client.delete.side_effect = [None, OSError("signed private credential")]
    storage.cleanup_product_ad_image(
        Factory(session), row.id, row.lease_token, account_id, product_id
    )
    if result == "success":
        assert [call.args[0] for call in client.delete.call_args_list] == [
            f"{prefix}/{width}.webp" for width in images.AD_IMAGE_SIZES
        ]
        assert (
            policy.managed_ad_image == {} and row.status == "succeeded" and row.outcome == "found"
        )
        assert row.lease_token is None
    else:
        assert policy.managed_ad_image == before
        if result == "transport-failure":
            assert row.status == "queued" and row.error == "Image cleanup failed: OSError"
            assert row.lease_token is None and row.available_at > NOW
            assert "private" not in row.error
        else:
            assert row.status == "running"
            connect.assert_not_called()


@pytest.mark.parametrize("kind", ["article", "product", "custom"])
def test_storage_wrappers_bind_the_right_subject_and_original_variant_policies(monkeypatch, kind):
    operation = Mock()
    monkeypatch.setattr(storage, "_store_asset", operation)
    factory, job_id, token, subject, account = (
        object(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    url = "https://publisher.example/image.png"
    if kind == "article":
        storage.store_image(factory, job_id, token, subject, url)
        from devfeed_core.models import Article

        assert operation.call_args.kwargs["model"] is Article
        assert operation.call_args.kwargs["original_store"] is storage.ImageOriginalStore
        assert operation.call_args.kwargs["variant_store"] is storage.ArticleThumbnailVariantStore
        assert operation.call_args.kwargs["source_field"] == "image_url"
        assert operation.call_args.args == (factory, job_id, token, subject, url)
    elif kind == "product":
        storage.store_product_image(factory, job_id, token, subject, url)
        assert operation.call_args.kwargs["model"] is PartnerProduct
        assert (
            operation.call_args.kwargs["original_store"]
            is product_image_storage.ProductImageOriginalStore
        )
        assert operation.call_args.kwargs["source_field"] == "product_url"
        assert operation.call_args.args == (factory, job_id, token, subject, url)
    else:
        storage.store_product_ad_image(factory, job_id, token, account, subject, url)
        assert operation.call_args.kwargs["model"] is PartnerProductPromotion
        assert operation.call_args.kwargs["original_store"] is storage.ImageOriginalStore
        assert operation.call_args.kwargs["asset_field"] == "managed_ad_image"
        assert operation.call_args.kwargs["source_field"] == "ad_image_url"
        assert operation.call_args.args == (factory, job_id, token, (account, subject), url)
    if kind != "article":
        assert operation.call_args.kwargs["variant_store"] is storage.ThumbnailVariantStore
    assert operation.call_args.kwargs["method"] == "r2-imgproxy"
