"""Chunked enrollment preserves all inventory and schedules new trust acceptance once."""

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from devfeed_core import logos, partner_product_images
from devfeed_core import partner_promotions as promotions
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerProduct,
    PartnerProductPromotion,
)
from devfeed_core.partner_promotions import ensure_automatic_assets
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.orm import Session

from tests.support.partner_accounts import account, campaign
from tests.support.partner_matching import NOW as PARTNER_NOW
from tests.support.partner_matching import article, product

NOW = datetime(2026, 10, 12, tzinfo=UTC)


class Rows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values

    def scalars(self):
        return self


class EnrollmentLedger:
    """Model database uniqueness at the query boundary without replacing enrollment."""

    def __init__(self, account, products):
        self.rows = [(account, product) for product in products]
        self.assets = {}
        self.approvals = set()
        self.asset_bind_counts = []
        self.approval_bind_counts = []

    def scalars(self, _statement):
        return []  # These fixtures authorize ordinary catalog placements only.

    def execute(self, statement):
        if not getattr(statement, "is_insert", False):
            entity = statement.column_descriptions[0]["entity"]
            if entity is PartnerAccount:
                return Rows(self.rows)
            assert entity is PartnerProductPromotion
            return Rows([])  # No explicit product exclusions in this catalog.
        params = statement.compile(dialect=dialect()).params
        product_ids = [value for key, value in params.items() if key.startswith("product_id")]
        if statement.table.name == PartnerProductPromotion.__tablename__:
            self.approval_bind_counts.append(len(params))
            newly_accepted = set(product_ids) - self.approvals
            self.approvals.update(product_ids)
            return Rows(list(newly_accepted))
        assert statement.table.name == PartnerAsset.__tablename__
        self.asset_bind_counts.append(len(params))
        account_ids = [value for key, value in params.items() if key.startswith("account_id")]
        campaign_ids = [value for key, value in params.items() if key.startswith("campaign_id")]
        formats = [value for key, value in params.items() if key.startswith("ad_format")]
        new_assets = []
        for key in zip(account_ids, product_ids, campaign_ids, formats, strict=True):
            if key not in self.assets:
                self.assets[key] = uuid.uuid4()
                new_assets.append((self.assets[key],))
        return Rows(new_assets)


@pytest.mark.parametrize("trusted", [False, True], ids=["qualified", "trusted"])
def test_large_enrollment_preserves_inventory_idempotency_and_new_acceptance_jobs(
    monkeypatch, trusted
):
    from devfeed_core import logos, partner_product_images

    logo_requests, image_requests = Mock(), Mock()
    monkeypatch.setattr(logos, "request_logo", logo_requests)
    monkeypatch.setattr(partner_product_images, "request_product_image", image_requests)
    account = PartnerAccount(
        id=uuid.uuid4(),
        name="Large ordinary catalog",
        tier="bronze",
        status="active",
        promotion_kind="product",
        organic_enabled=True,
        auto_include_products=True,
        approval_mode="trusted" if trusted else "qualified",
        ad_formats=["feed"],
    )
    products = [
        PartnerProduct(
            id=uuid.uuid4(), name=f"Tool {index}", status="pending" if trusted else "approved"
        )
        for index in range(1201)
    ]
    ledger = EnrollmentLedger(account, products)

    assert ensure_automatic_assets(ledger, account.id, NOW) == len(products)
    original_assets = ledger.assets.copy()
    assert {key[1] for key in ledger.assets} == {product.id for product in products}
    assert len(ledger.asset_bind_counts) > 1
    assert max(ledger.asset_bind_counts) <= 6000
    assert ensure_automatic_assets(ledger, account.id, NOW) == 0
    assert ledger.assets == original_assets
    expected_accepted = {product.id for product in products} if trusted else set()
    assert ledger.approvals == expected_accepted
    assert {call.args[2] for call in logo_requests.call_args_list} == expected_accepted
    assert {call.args[1] for call in image_requests.call_args_list} == expected_accepted
    assert logo_requests.call_count == image_requests.call_count == len(expected_accepted)
    assert {product.status for product in products} == ({"pending"} if trusted else {"approved"})
    if trusted:
        assert len(ledger.approval_bind_counts) > 1
        # Managed-image defaults add bindings while each trusted batch stays bounded.
        assert max(ledger.approval_bind_counts) <= 4000


@pytest.mark.parametrize("new_acceptance", [False, True])
@pytest.mark.parametrize("inserted", ["all", "organic", "none"])
def test_trusted_enrollment_schedules_only_new_acceptance_and_dirties_only_new_sponsorship(
    monkeypatch, new_acceptance, inserted
):
    owner = account(approval_mode="trusted", ad_formats=["feed", "sponsored_content"])
    values = [
        product(
            id=uuid.UUID(int=index),
            name=f"API checker {index}",
            product_url=f"https://checker{index}.example/",
            status="pending",
            verified_at=None,
        )
        for index in (1, 2)
    ]
    story = article()
    agreement = campaign(owner, ad_format="sponsored_content", article_id=story.id)
    generated = [uuid.UUID(int=index) for index in range(101, 105)]
    sequence = iter(generated)
    # Limit deterministic entropy to this module; UUID generation elsewhere remains real.
    monkeypatch.setattr(promotions, "uuid", SimpleNamespace(uuid4=lambda: next(sequence)))
    session = Mock(spec=Session)
    acceptance = Mock()
    acceptance.scalars.return_value.all.return_value = [values[0].id] if new_acceptance else []
    exclusions = Mock()
    exclusions.all.return_value = []
    asset_insert = Mock()
    returned = {"all": generated, "organic": generated[::2], "none": []}[inserted]
    asset_insert.all.return_value = [(identifier,) for identifier in returned]
    session.execute.side_effect = [acceptance, exclusions, asset_insert]
    session.scalars.return_value = [agreement]
    managed = Mock()
    monkeypatch.setattr(promotions, "ensure_managed_campaign", managed)
    logo, image, dirty = Mock(), Mock(), Mock()
    monkeypatch.setattr(logos, "request_logo", logo)
    monkeypatch.setattr(partner_product_images, "request_product_image", image)
    monkeypatch.setattr(promotions, "mark_dirty", dirty)

    created = promotions._enroll_automatic_rows(
        session, [(owner, value) for value in values], PARTNER_NOW
    )

    assert created == len(returned)
    assert [value.status for value in values] == ["pending", "pending"]
    assert all(value.verified_at is None for value in values)
    managed.assert_has_calls([call(session, owner, PARTNER_NOW, kind) for kind in owner.ad_formats])
    if new_acceptance:
        logo.assert_called_once_with(session, type(values[0]), values[0].id, automatic=True)
        image.assert_called_once_with(session, values[0].id, automatic=True)
    else:
        logo.assert_not_called()
        image.assert_not_called()
    if inserted == "all":
        dirty.assert_called_once_with(session, "partner_assets")
    else:
        dirty.assert_not_called()
    session.commit.assert_not_called()


def test_empty_enrollment_has_no_campaign_or_media_side_effects():
    session = Mock(spec=Session)
    assert promotions._enroll_automatic_rows(session, [], PARTNER_NOW) == 0
    assert session.mock_calls == []


@pytest.mark.parametrize("eligible", [False, True])
def test_catalog_repair_returns_enrollment_result_only_for_the_supplied_owned_rows(
    monkeypatch, eligible
):
    owner, value, session = account(), product(), Mock(spec=Session)
    rows = [(owner, value)] if eligible else []
    session.execute.return_value.all.return_value = rows
    enroll = Mock(return_value=2)
    monkeypatch.setattr(promotions, "_enroll_automatic_rows", enroll)
    assert promotions.ensure_automatic_assets(session, owner.id, PARTNER_NOW, [value.id]) == (
        2 if eligible else 0
    )
    if eligible:
        enroll.assert_called_once_with(session, rows, PARTNER_NOW)
    else:
        enroll.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("admitted", [False, True])
def test_bounded_repair_keeps_completed_enrollment_count_when_the_periodic_lane_has_no_more_work(
    monkeypatch, admitted
):
    session, factory = Mock(spec=Session), Mock()
    factory.begin.return_value = nullcontext(session)
    session.scalar.return_value = admitted
    session.execute.return_value.all.return_value = []
    pending = Mock(return_value=3)
    monkeypatch.setattr(promotions, "process_promotion_enrollment", pending)
    assert promotions.sync_partner_promotions(factory, limit=25, now=PARTNER_NOW) == 3
    pending.assert_called_once_with(factory, limit=25, now=PARTNER_NOW)
    if admitted:
        session.execute.assert_called_once()
    else:
        session.execute.assert_not_called()
