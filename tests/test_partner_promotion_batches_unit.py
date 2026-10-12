"""Chunked enrollment preserves all inventory and schedules new trust acceptance once."""

import uuid
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerProduct,
    PartnerProductPromotion,
)
from devfeed_core.partner_promotions import ensure_automatic_assets
from sqlalchemy.dialects.postgresql import dialect

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
        assert max(ledger.approval_bind_counts) <= 3000
