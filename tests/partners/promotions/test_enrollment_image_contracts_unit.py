"""Automatic asset planning and product-preview admission preserve explicit choices."""

import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import partner_product_images as images
from devfeed_core import partner_promotions as promotions
from devfeed_core.models import ArticleImageJob, PartnerAsset
from devfeed_core.services import RecordNotFound
from sqlalchemy.orm import Session

from tests.partners.catalog.test_catalog_commands_unit import managed
from tests.support.partner_accounts import account, campaign
from tests.support.partner_matching import NOW, product, result
from tests.support.source_readiness import configuration


@pytest.mark.parametrize(
    "case",
    [
        "ordinary",
        "explicit",
        "managed",
        "excluded",
        "unqualified",
        "trusted",
        "organic_off",
        "auto_off",
        "auto_off_selected",
        "managed_wrong_kind",
        "bronze",
        "wrong_product",
    ],
)
def test_enrollment_plan_keeps_paid_and_ordinary_authority_independent(case):
    owner, value = account(), product()
    agreement = campaign(owner)
    excluded = set()
    if case == "explicit":
        owner.organic_enabled = False
        agreement.targeting, agreement.product_ids = "selected", [str(value.id)]
    elif case == "managed":
        owner.promotion_kind, agreement.managed = "ad", True
    elif case == "excluded":
        excluded.add((owner.id, value.id))
    elif case in {"unqualified", "trusted"}:
        value.status, value.verified_at = "pending", None
        if case == "trusted":
            owner.approval_mode = "trusted"
    elif case == "organic_off":
        owner.organic_enabled = False
    elif case.startswith("auto_off"):
        owner.auto_include_products = False
        if case == "auto_off_selected":
            agreement.targeting, agreement.product_ids = "selected", [str(value.id)]
    elif case == "managed_wrong_kind":
        agreement.managed = True
    elif case == "bronze":
        owner.tier = "bronze"
    elif case == "wrong_product":
        agreement.targeting, agreement.product_ids = "selected", [str(uuid.uuid4())]
    values = list(
        promotions._automatic_asset_values([(owner, value)], {owner.id: [agreement]}, excluded)
    )
    expected_paid = case not in {
        "excluded",
        "unqualified",
        "auto_off",
        "managed_wrong_kind",
        "bronze",
        "wrong_product",
    }
    expected_ordinary = case not in {
        "explicit",
        "managed",
        "excluded",
        "unqualified",
        "organic_off",
        "auto_off",
        "auto_off_selected",
    }
    assert [row["campaign_id"] for row in values] == ([None] if expected_ordinary else []) + (
        [agreement.id] if expected_paid else []
    )
    for fields in values:
        created = PartnerAsset(**fields)
        assert created.account_id == owner.id and created.product_id == value.id
        assert created.kind == ("ad" if created.campaign_id else "product")
        assert created.automatic and created.status == "active"
        assert created.name == value.name


@pytest.fixture
def image_policy(monkeypatch):
    settings = configuration(image_storage_enabled=True)
    monkeypatch.setattr(images, "get_settings", lambda: settings)
    return settings


@pytest.mark.parametrize(
    "stop", ["disabled", "missing", "excluded", "merged", "current", "active", "previous_attempt"]
)
def test_product_image_admission_preserves_current_work_and_prevents_automatic_retry_loops(
    image_policy, stop
):
    value, session = product(managed_image={}), Mock(spec=Session)
    if stop == "disabled":
        image_policy.image_storage_enabled = False
    if stop == "excluded":
        value.excluded, value.status = True, "paused"
    if stop == "merged":
        value.merged_into_id, value.status = uuid.uuid4(), "withdrawn"
    if stop == "current":
        value.managed_image = {
            **managed("https://checker.example/preview.png"),
            "page_url": value.product_url,
        }
    active = ArticleImageJob(
        id=uuid.uuid4(),
        product_id=value.id,
        operation="product-image",
        image_url=value.product_url,
        status="queued",
        attempts=0,
        available_at=NOW,
        created_at=NOW,
        storage_version=images.THUMBNAIL_VERSION,
    )
    if stop == "previous_attempt":
        active.status, active.attempts = "failed", 3
        active.created_at = active.available_at = NOW - timedelta(minutes=2)
        active.finished_at = NOW - timedelta(minutes=1)
    session.scalar.side_effect = [
        None if stop == "missing" else value,
        active if stop == "active" else None,
        active.id if stop == "previous_attempt" else None,
    ]
    if stop == "missing":
        with pytest.raises(RecordNotFound):
            images.request_product_image(session, value.id, automatic=True)
    else:
        chosen = images.request_product_image(session, value.id, automatic=True)
        assert chosen is (active if stop == "active" else None)
    session.add.assert_not_called()
    if stop == "disabled":
        session.scalar.assert_not_called()


def test_explicit_product_image_retry_can_repeat_an_old_failed_attempt(image_policy):
    value, session = product(managed_image={}), Mock(spec=Session)
    session.scalar.side_effect = [value, None]
    queued = images.request_product_image(session, value.id)
    assert queued.product_id == value.id and queued.operation == "product-image"
    assert (
        queued.image_url == value.product_url and queued.storage_version == images.THUMBNAIL_VERSION
    )
    session.add.assert_called_once_with(queued)
    assert session.scalar.call_count == 2


@pytest.mark.parametrize("limit", [0, 501])
def test_product_preview_backfill_rejects_unbounded_request_before_read(image_policy, limit):
    session = Mock(spec=Session)
    with pytest.raises(ValueError, match="1 and 500"):
        images.backfill_product_images(session, limit)
    assert session.mock_calls == []


def test_backfill_returns_only_admitted_jobs_from_the_bounded_catalog_read(
    image_policy, monkeypatch
):
    ids, session = [uuid.uuid4(), uuid.uuid4()], Mock(spec=Session)
    session.scalars.return_value = result(ids)
    admitted = ArticleImageJob(
        id=uuid.uuid4(),
        product_id=ids[0],
        operation="product-image",
        image_url="https://checker.example/",
        status="queued",
        attempts=0,
        created_at=NOW,
        available_at=NOW,
        storage_version=images.THUMBNAIL_VERSION,
    )
    request = Mock(side_effect=[admitted, None])
    monkeypatch.setattr(images, "request_product_image", request)
    assert images.backfill_product_images(session, limit=2) == [admitted]
    assert [call.args[1] for call in request.call_args_list] == ids
    assert all(call.kwargs == {"automatic": True} for call in request.call_args_list)


def test_disabled_image_backfill_leaves_catalog_untouched(image_policy):
    image_policy.image_storage_enabled = False
    session = Mock(spec=Session)
    assert images.backfill_product_images(session) == []
    session.scalars.assert_not_called()
