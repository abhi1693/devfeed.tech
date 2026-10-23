"""Readers skip concurrent edits while valid tracking receipts remain retriable."""

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerConnection,
    PartnerListing,
    PartnerProduct,
    PartnerProductPromotion,
)
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.partner_tracking import (
    BusyPartnerAsset,
    PartnerTrackingTokens,
    record_delivery_event,
)
from sqlalchemy import select, text

from tests.support.partner_placements import KEY, deliver, query
from tests.support.partner_placements import catalog as catalog_fixture

catalog = catalog_fixture

pytestmark = pytest.mark.integration


def locked_row(session, kind, catalog):
    if kind == "account":
        model, identifier = PartnerAccount, catalog["paid"]
    elif kind == "product":
        model, identifier = PartnerProduct, catalog["paid_product"]
    elif kind == "campaign":
        model, identifier = PartnerCampaign, catalog["campaign"]
    elif kind == "connection":
        return session.scalar(
            select(PartnerConnection).where(PartnerConnection.provider == "paid").with_for_update()
        )
    elif kind == "listing":
        return session.scalar(
            select(PartnerListing)
            .where(PartnerListing.product_id == catalog["paid_product"])
            .with_for_update()
        )
    elif kind == "exclusion":
        return session.scalar(
            select(PartnerProductPromotion)
            .where(
                PartnerProductPromotion.account_id == catalog["paid"],
                PartnerProductPromotion.product_id == catalog["paid_product"],
            )
            .with_for_update()
        )
    else:
        model = PartnerAsset
        identifier = session.scalar(
            select(PartnerAsset.id).where(
                PartnerAsset.campaign_id == catalog["campaign"],
                PartnerAsset.product_id == catalog["paid_product"],
            )
        )
    return session.scalar(select(model).where(model.id == identifier).with_for_update())


@pytest.mark.parametrize(
    "kind", ["account", "asset", "product", "campaign", "connection", "listing", "exclusion"]
)
def test_reader_skips_busy_attribution_without_waiting(database, catalog, kind):
    if kind == "exclusion":
        with database.begin() as setup:
            setup.add(
                PartnerProductPromotion(
                    account_id=catalog["paid"], product_id=catalog["paid_product"], excluded=False
                )
            )
    with database.begin() as editor:
        assert locked_row(editor, kind, catalog) is not None
        with database.begin() as reader:
            reader.execute(text("SET LOCAL lock_timeout = '100ms'"))
            items = deliver_placements(reader, query(catalog), KEY)
        assert not any(item["sponsored"] for item in items)
    assert any(item["sponsored"] for item in deliver(database, query(catalog)))


@pytest.mark.parametrize(
    "kind", ["account", "asset", "product", "campaign", "connection", "listing", "exclusion"]
)
def test_tracking_busy_attribution_is_retriable_and_same_receipt_records_later(
    database, catalog, kind
):
    sponsored = next(item for item in deliver(database, query(catalog)) if item["sponsored"])
    receipt = PartnerTrackingTokens(KEY).verify(sponsored["receipt"])
    if kind == "exclusion":
        with database.begin() as setup:
            setup.add(
                PartnerProductPromotion(
                    account_id=catalog["paid"], product_id=catalog["paid_product"], excluded=False
                )
            )
    with database.begin() as editor:
        assert locked_row(editor, kind, catalog) is not None
        with database.begin() as reporter, pytest.raises(BusyPartnerAsset):
            reporter.execute(text("SET LOCAL lock_timeout = '100ms'"))
            record_delivery_event(reporter, receipt, "impression")
    with database.begin() as reporter:
        assert record_delivery_event(reporter, receipt, "impression")
        assert not record_delivery_event(reporter, receipt, "impression")
