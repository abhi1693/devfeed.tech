"""Canonical products keep each advertiser's live catalog copy in placements."""

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerConnection,
    PartnerListing,
    PartnerManualProduct,
    PartnerProduct,
    utcnow,
)
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_http.partner_portal import creative_preview
from sqlalchemy import select
from test_partner_placement_rotation import issue, seed_rotation

pytestmark = pytest.mark.integration
FIRST_TAGLINE = "Monitor Python jobs from the first partner's catalog."
SECOND_TAGLINE = "Inspect Python task metrics from the second partner's catalog."


def canonical_facts(session, product_id):
    product = session.get(PartnerProduct, product_id)
    return (
        product.name,
        product.tagline,
        product.product_url,
        product.technologies,
        product.status,
        product.revision,
        product.updated_at,
        product.verified_at,
    )


@pytest.fixture
def shared_catalog(database):
    catalog = seed_rotation(database, count=1)
    with database.begin() as session:
        product_id = catalog["products"][0]
        product = session.get(PartnerProduct, product_id)
        listing = session.scalar(
            select(PartnerListing).where(PartnerListing.provider == "rotation")
        )
        listing.tagline = FIRST_TAGLINE
        listing.attribution = "Via First platform"
        other = PartnerAccount(name="Second sponsor", tier="silver")
        session.add(other)
        session.flush()
        session.add(PartnerConnection(provider="second", account_id=other.id, enabled=True))
        session.flush()
        session.add(
            PartnerListing(
                provider="second",
                external_id="same-product",
                product_id=product_id,
                name=product.name,
                product_url=product.product_url,
                listing_url="https://second.example/catalog/python-tool",
                tagline=SECOND_TAGLINE,
                attribution="Via Second platform",
            )
        )
        session.flush()
        ensure_automatic_assets(session, other.id, catalog["now"])
        other.status = "paused"
        return {
            **catalog,
            "other_account": other.id,
            "listing": listing.id,
            "canonical": canonical_facts(session, product_id),
        }


@pytest.mark.parametrize("destination", ["product_url", "listing_url"])
def test_delivery_and_preview_use_own_listing_without_changing_canonical_facts(
    database, shared_catalog, destination
):
    catalog = shared_catalog
    product_id = catalog["products"][0]
    accounts = ((catalog["account"], FIRST_TAGLINE), (catalog["other_account"], SECOND_TAGLINE))
    for account_id, tagline in accounts:
        with database.begin() as session:
            account = session.get(PartnerAccount, account_id)
            account.outbound_destination = destination
            account.status = "active"
            for other_id, _ in accounts:
                if other_id != account_id:
                    session.get(PartnerAccount, other_id).status = "paused"
            session.flush()
            preview = creative_preview(session, account, product_id).product
            assert preview is not None
            assert preview.id == product_id
            assert preview.tagline == tagline
            assert preview.product_url == "https://product0.example/?utm_source=devfeed"
            assert preview.listing_url == (
                f"https://catalog.example/products/{product_id}?utm_source=devfeed"
                if account_id == catalog["account"]
                else "https://second.example/catalog/python-tool?utm_source=devfeed"
            )
        delivered = issue(database, catalog)
        assert len(delivered) == 1
        item = delivered[0]
        assert item["product_id"] == product_id
        assert item["tagline"] == tagline
        assert item["attribution"] == (
            "Via First platform" if account_id == catalog["account"] else "Via Second platform"
        )
        assert item["url"] == (
            preview.listing_url if destination == "listing_url" else preview.product_url
        )
        with database() as session:
            assert session.get(PartnerAsset, item["asset_id"]).account_id == account_id
            assert canonical_facts(session, product_id) == catalog["canonical"]


@pytest.mark.parametrize("destination", ["product_url", "listing_url"])
def test_creative_copy_and_listing_destination_come_from_same_usable_owned_listing(
    database, shared_catalog, destination
):
    catalog = shared_catalog
    product_id = catalog["products"][0]
    tagline = "Python monitoring from this account's usable listing."
    listing_url = "https://first.example/catalog/python-tool?utm_source=devfeed"
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["account"])
        account.outbound_destination = destination
        session.get(PartnerListing, catalog["listing"]).listing_url = "http://127.0.0.1/private"
        product = session.get(PartnerProduct, product_id)
        session.add(
            PartnerListing(
                provider="rotation",
                external_id="zz-working-link",
                product_id=product_id,
                name=product.name,
                product_url=product.product_url,
                listing_url="https://first.example/catalog/python-tool",
                tagline=tagline,
            )
        )
        session.flush()
        preview = creative_preview(session, account, product_id).product
        assert preview is not None
        assert preview.tagline == tagline
        assert preview.listing_url == listing_url
    delivered = issue(database, catalog)
    assert len(delivered) == 1
    assert delivered[0]["tagline"] == tagline
    assert delivered[0]["url"] == (
        listing_url if destination == "listing_url" else preview.product_url
    )
    with database() as session:
        assert canonical_facts(session, product_id) == catalog["canonical"]


@pytest.mark.parametrize("destination", ["product_url", "listing_url"])
def test_active_manual_source_uses_canonical_copy_when_import_is_disabled(database, destination):
    catalog = seed_rotation(database, count=1)
    product_id = catalog["products"][0]
    with database.begin() as session:
        before = canonical_facts(session, product_id)
        account = session.get(PartnerAccount, catalog["account"])
        account.outbound_destination = destination
        session.get(PartnerConnection, "rotation").enabled = False
        listing = session.scalar(select(PartnerListing))
        listing.tagline = "An unavailable imported tagline must not leak into manual placements."
        session.add(PartnerManualProduct(account_id=account.id, product_id=product_id))
        session.flush()
        preview = creative_preview(session, account, product_id).product
        assert preview is not None
        assert preview.tagline == before[1]
        assert preview.listing_url == preview.product_url
    delivered = issue(database, catalog)
    assert len(delivered) == 1
    assert delivered[0]["tagline"] == before[1]
    assert delivered[0]["url"] == preview.product_url
    with database() as session:
        assert canonical_facts(session, product_id) == before


@pytest.mark.parametrize(
    "unavailable",
    ["disabled", "archived", "inactive", "unresolved", "foreign-account", "inactive-manual"],
)
def test_unavailable_sources_never_borrow_foreign_or_canonical_creative(
    database, shared_catalog, unavailable
):
    catalog = shared_catalog
    product_id = catalog["products"][0]
    with database.begin() as session:
        connection = session.get(PartnerConnection, "rotation")
        listing = session.get(PartnerListing, catalog["listing"])
        if unavailable in {"disabled", "inactive-manual"}:
            connection.enabled = False
        elif unavailable == "archived":
            connection.archived_at = utcnow()
        elif unavailable == "inactive":
            listing.active = False
        elif unavailable == "unresolved":
            listing.identity_status = "unresolved"
        else:
            connection.account_id = catalog["other_account"]
        if unavailable == "inactive-manual":
            session.add(
                PartnerManualProduct(
                    account_id=catalog["account"], product_id=product_id, active=False
                )
            )
        session.flush()
        account = session.get(PartnerAccount, catalog["account"])
        assert creative_preview(session, account, product_id).product is None
    assert issue(database, catalog) == []
    with database() as session:
        assert canonical_facts(session, product_id) == catalog["canonical"]
