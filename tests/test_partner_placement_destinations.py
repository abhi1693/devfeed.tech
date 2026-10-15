"""Batch destination checks preserve account ownership and final resolver boundaries."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerConnection,
    PartnerListing,
    PartnerManualProduct,
    PartnerProduct,
)
from devfeed_core.partner_placement_destinations import (
    PlacementDestinations,
    account_product_creative,
)
from sqlalchemy import select
from sqlalchemy.exc import OperationalError


@pytest.fixture
def destination_catalog(database):
    with database.begin() as session:
        accounts = [
            PartnerAccount(name=name, tier="silver", outbound_destination="listing_url")
            for name in ("First partner", "Second partner")
        ]
        products = [
            PartnerProduct(
                name=name,
                tagline="A developer tool",
                product_url=f"https://tools.example/{index}",
            )
            for index, name in enumerate(("First tool", "Second tool"))
        ]
        session.add_all([*accounts, *products])
        session.flush()
        session.add_all(
            PartnerConnection(provider=provider, account_id=account.id, enabled=True)
            for provider, account in zip(("first", "second"), accounts, strict=True)
        )
        return [account.id for account in accounts], [product.id for product in products]


def listing(provider, product, external_id, url):
    return PartnerListing(
        provider=provider,
        product_id=product.id,
        external_id=external_id,
        name=product.name,
        tagline=product.tagline,
        product_url=product.product_url,
        listing_url=url,
    )


def destination_row(account, product, ad_format="feed"):
    return SimpleNamespace(ad_format=ad_format), account, product, None


@pytest.mark.integration
def test_batch_destinations_never_borrow_another_accounts_listing(database, destination_catalog):
    account_ids, product_ids = destination_catalog
    with database.begin() as session:
        products = [session.get(PartnerProduct, identifier) for identifier in product_ids]
        session.add_all(
            listing(provider, product, str(index), url)
            for provider, product, index, url in (
                ("first", products[0], 0, ""),
                ("first", products[1], 1, "https://first.example/tool"),
                ("second", products[0], 0, "https://second.example/tool"),
                ("second", products[1], 1, ""),
            )
        )
    with database.begin() as session:
        accounts = [session.get(PartnerAccount, identifier) for identifier in account_ids]
        products = [session.get(PartnerProduct, identifier) for identifier in product_ids]
        rows = [destination_row(account, product) for account in accounts for product in products]

        eligible = PlacementDestinations(session).eligible_rows(rows)

        assert [(row[1].id, row[2].id) for row in eligible] == [
            (account_ids[0], product_ids[1]),
            (account_ids[1], product_ids[0]),
        ]
        assert eligible == [
            row
            for row in rows
            if (creative := account_product_creative(session, row[1], row[2])) is not None
            and creative.destination(row[1], row[2]) is not None
        ]


@pytest.mark.integration
@pytest.mark.parametrize("valid_position", [19, 20], ids=["twentieth", "twenty-first"])
def test_batch_listing_limit_precedes_url_validation(database, destination_catalog, valid_position):
    account_ids, product_ids = destination_catalog
    with database.begin() as session:
        product = session.get(PartnerProduct, product_ids[0])
        session.add_all(
            listing(
                "first",
                product,
                f"{index:02}",
                "https://catalog.example/valid" if index == valid_position else "",
            )
            for index in range(21)
        )
    with database.begin() as session:
        account = session.get(PartnerAccount, account_ids[0])
        product = session.get(PartnerProduct, product_ids[0])
        row = destination_row(account, product)

        assert PlacementDestinations(session).eligible_rows([row]) == (
            [row] if valid_position < 20 else []
        )
        creative = account_product_creative(session, account, product, lock=True)
        assert (creative.destination(account, product) is not None) == (valid_position < 20)


@pytest.mark.integration
@pytest.mark.parametrize("state", ["active", "inactive", "merged", "unsafe-url"])
def test_batch_manual_fallback_requires_active_owned_unmerged_safe_product(
    database, destination_catalog, state
):
    account_ids, product_ids = destination_catalog
    with database.begin() as session:
        product = session.get(PartnerProduct, product_ids[0])
        session.add(listing("first", product, "unsafe", ""))
        session.add(
            PartnerManualProduct(
                account_id=account_ids[0], product_id=product.id, active=state != "inactive"
            )
        )
        if state == "merged":
            product.merged_into_id = product_ids[1]
        elif state == "unsafe-url":
            product.product_url = "http://127.0.0.1/private"
    with database.begin() as session:
        account = session.get(PartnerAccount, account_ids[0])
        other_account = session.get(PartnerAccount, account_ids[1])
        product = session.get(PartnerProduct, product_ids[0])
        row = destination_row(account, product)
        unowned = destination_row(other_account, product)

        assert PlacementDestinations(session).eligible_rows([row, unowned]) == (
            [row] if state == "active" else []
        )
        creative = account_product_creative(session, account, product, lock=True)
        assert (creative.destination(account, product) is not None) == (state == "active")
        assert account_product_creative(session, other_account, product, lock=True) is None


def test_sponsored_article_destination_ignores_unavailable_product_destination():
    session = Mock()
    session.execute.side_effect = AssertionError("Article destinations need no listing reads")
    account = SimpleNamespace(id="account", outbound_destination="listing_url")
    product = SimpleNamespace(id="product", product_url="http://127.0.0.1/private")
    story = destination_row(account, product, "sponsored_content")
    feed = destination_row(account, product)
    destinations = PlacementDestinations(session)

    assert destinations.eligible_rows([story]) == [story]
    destinations.reject(account.id, product.id)
    assert destinations.eligible_rows([feed, story]) == [story]
    session.execute.assert_not_called()


@pytest.mark.parametrize("destination", ["product_url", "listing_url"])
def test_account_creative_uses_the_copy_for_its_selected_destination(destination):
    session = Mock()
    session.execute.return_value = [
        SimpleNamespace(
            tagline="First owned source copy",
            listing_url="",
            provider="first",
            name="First platform",
            logo_url=None,
            managed_logo={},
        ),
        SimpleNamespace(
            tagline="Copy for the working link",
            listing_url="https://catalog.example/tool",
            provider="second",
            name="Working platform",
            logo_url=None,
            managed_logo={},
        ),
    ]
    account = SimpleNamespace(id="account", outbound_destination=destination)
    product = SimpleNamespace(id="product", tagline="Another account's canonical copy")
    creative = account_product_creative(session, account, product)
    assert creative.tagline == "Copy for the working link"
    assert creative.listing_url == "https://catalog.example/tool?utm_source=devfeed"
    assert creative.platform.name == "Working platform"
    session.execute.assert_called_once()


def test_missing_account_creative_does_not_use_canonical_copy_without_manual_ownership():
    session = Mock()
    session.execute.side_effect = [[], Mock(first=lambda: None)]
    account = SimpleNamespace(id="account", outbound_destination="product_url")
    product = SimpleNamespace(id="product", tagline="Foreign canonical copy")
    assert account_product_creative(session, account, product) is None


@pytest.mark.integration
def test_account_creative_reads_committed_copy_despite_an_old_orm_listing(
    database, destination_catalog
):
    account_ids, product_ids = destination_catalog
    with database.begin() as session:
        product = session.get(PartnerProduct, product_ids[0])
        source = listing("first", product, "tool", "https://catalog.example/tool")
        source.tagline = "Previous imported copy"
        session.add(source)
        session.flush()
        identifier = source.id
    with database.begin() as reader:
        cached = reader.get(PartnerListing, identifier)
        with database.begin() as editor:
            editor.get(PartnerListing, identifier).tagline = "Current imported copy"
        creative = account_product_creative(
            reader,
            reader.get(PartnerAccount, account_ids[0]),
            reader.get(PartnerProduct, product_ids[0]),
            lock=True,
        )
        assert cached.tagline == "Previous imported copy"
        assert creative.tagline == "Current imported copy"


@pytest.mark.integration
def test_busy_owned_creative_is_skipped_without_foreign_copy_fallback(
    database, destination_catalog
):
    account_ids, product_ids = destination_catalog
    with database.begin() as session:
        product = session.get(PartnerProduct, product_ids[0])
        ours = listing("first", product, "tool", "https://first.example/tool")
        foreign = listing("second", product, "tool", "https://second.example/tool")
        ours.tagline, foreign.tagline = "Our imported copy", "Foreign imported copy"
        session.add_all([ours, foreign])
        session.flush()
        identifier = ours.id
    with database.begin() as editor:
        editor.scalar(
            select(PartnerListing).where(PartnerListing.id == identifier).with_for_update()
        ).tagline = "Uncommitted replacement"
        editor.flush()
        with database.begin() as reader:
            assert (
                account_product_creative(
                    reader,
                    reader.get(PartnerAccount, account_ids[0]),
                    reader.get(PartnerProduct, product_ids[0]),
                    lock=True,
                )
                is None
            )
    with database.begin() as reader:
        creative = account_product_creative(
            reader,
            reader.get(PartnerAccount, account_ids[0]),
            reader.get(PartnerProduct, product_ids[0]),
            lock=True,
        )
        assert creative.tagline == "Uncommitted replacement"
        with database.begin() as editor, pytest.raises(OperationalError) as conflict:
            editor.scalar(
                select(PartnerListing)
                .where(PartnerListing.id == identifier)
                .with_for_update(nowait=True)
            )
        assert conflict.value.orig.sqlstate == "55P03"
