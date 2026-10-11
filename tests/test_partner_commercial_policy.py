"""Tier inheritance, scoped trusted admission and explicitly bounded sponsored fallback."""

import importlib.util
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerConnection,
    PartnerListing,
    PartnerProduct,
    PartnerProductPromotion,
    utcnow,
)
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.partner_promotions import (
    ensure_automatic_assets,
    sync_partner_promotions,
    trusted_product_exists,
)
from devfeed_core.partner_tiers import effective_promotion_kind, tier_policy
from devfeed_core.partner_tracking import (
    InactivePartnerAsset,
    PartnerTrackingTokens,
    record_delivery_event,
)
from devfeed_http.partner_placements import PlacementQuery
from devfeed_http.partner_portal import AccountInput, PartnerPreferencesInput
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from test_partner_placements_integration import KEY
from test_partner_placements_integration import catalog as catalog_fixture
from test_partner_portal import admin_portal_client as admin_client_fixture
from test_partner_portal import portal_client as portal_client_fixture
from test_partner_portal import seed

catalog = catalog_fixture
portal_client = portal_client_fixture
admin_client = admin_client_fixture


@pytest.mark.parametrize(
    "tier,kind",
    [
        ("bronze", "product"),
        ("silver", "ad"),
        ("gold", "ad"),
        ("platinum", "ad"),
        ("diamond", "ad"),
    ],
)
def test_tiers_define_enforced_types_and_inherited_defaults(tier, kind):
    account = PartnerAccount(tier=tier)
    assert effective_promotion_kind(account) == kind
    assert tier_policy(tier)["allowed_promotion_kinds"] == (
        ("product",) if kind == "product" else ("product", "ad")
    )
    account.promotion_kind = "product"
    assert effective_promotion_kind(account) == "product"


@pytest.mark.parametrize("tier", ["bronze"])
def test_account_cannot_override_missing_tier_entitlement(tier):
    with pytest.raises(ValidationError):
        AccountInput(name="Partner", tier=tier, promotion_kind="ad")


@pytest.mark.parametrize("surface", ["article", "topic"])
def test_fallback_is_an_explicit_main_feed_capability(surface):
    with pytest.raises(ValidationError):
        PlacementQuery(
            surface=surface, **{f"{surface}_id": uuid.uuid4()}, allow_sponsored_fallback=True
        )
    assert not PlacementQuery(surface="feed").allow_sponsored_fallback


@pytest.mark.parametrize(
    "forged", [{"tier": "diamond"}, {"approval_mode": "trusted"}, {"promotion_kind": "ad"}]
)
def test_partner_preferences_cannot_change_commercial_authority(forged):
    with pytest.raises(ValidationError):
        PartnerPreferencesInput(outbound_destination="product_url", **forged)


def configure_trusted(database, catalog, **overrides):
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["paid"])
        account.tier, account.promotion_kind, account.approval_mode = "gold", None, "trusted"
        for key, value in overrides.items():
            setattr(account, key, value)
        product = session.get(PartnerProduct, catalog["paid_product"])
        product.status, product.verified_at = "rejected", None
        product.technologies, product.evidence = [], []
        product.assessment = {
            "state": "irrelevant",
            "reason": "Consumer product without developer capabilities",
        }
        product.updated_at = utcnow()
        session.get(PartnerProduct, catalog["unrelated"]).excluded = True
        session.get(PartnerCampaign, catalog["campaign"]).status = "ended"
        session.flush()
        ensure_automatic_assets(session, account.id)


def fallback(database, **kwargs):
    with database.begin() as session:
        return deliver_placements(
            session, PlacementQuery(surface="feed", allow_sponsored_fallback=True, **kwargs), KEY
        )


@pytest.mark.integration
def test_trusted_rejected_import_is_sponsored_without_fabricating_facts_or_context(
    database, catalog
):
    configure_trusted(database, catalog)
    items = fallback(database)
    assert len(items) == 1 and items[0]["sponsored"]
    assert items[0]["product_id"] == catalog["paid_product"]
    assert items[0]["reason"] == "Sponsored by this partner"
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        assert (product.status, product.verified_at, product.technologies, product.evidence) == (
            "rejected",
            None,
            [],
            [],
        )
        promotion = session.get(PartnerProductPromotion, (catalog["paid"], product.id))
        assert promotion.approval_source == "trusted" and promotion.commercial_approved_at
        assert (
            session.scalar(
                select(PartnerProduct.id).where(
                    PartnerProduct.id == product.id, trusted_product_exists()
                )
            )
            == product.id
        )
        for query in [
            PlacementQuery(surface="feed"),
            PlacementQuery(surface="article", article_id=catalog["article"]),
            PlacementQuery(surface="topic", topic_id=catalog["topic"]),
        ]:
            assert not any(
                row["product_id"] == product.id for row in deliver_placements(session, query, KEY)
            )
        receipt = PartnerTrackingTokens(KEY).verify(items[0]["receipt"])
        assert record_delivery_event(session, receipt, "impression")
        assert not record_delivery_event(session, receipt, "impression")


@pytest.mark.integration
def test_commercial_acceptance_is_scoped_to_owned_account_not_canonical_metadata(database, catalog):
    configure_trusted(database, catalog)
    with database.begin() as session:
        session.add(
            PartnerListing(
                product_id=catalog["paid_product"],
                provider="free",
                external_id="duplicate",
                name="Same product",
                product_url="https://tools.example/paid_product",
                listing_url="https://free.example/duplicate",
                description="Shared identity",
            )
        )
        session.flush()
        ensure_automatic_assets(session, catalog["free"])
        assert (
            session.get(PartnerProductPromotion, (catalog["free"], catalog["paid_product"])) is None
        )
        assert (
            session.scalar(
                select(PartnerAsset.id).where(
                    PartnerAsset.account_id == catalog["free"],
                    PartnerAsset.product_id == catalog["paid_product"],
                )
            )
            is None
        )
        promotion = session.get(PartnerProductPromotion, (catalog["paid"], catalog["paid_product"]))
        before = promotion.commercial_approved_at
        assert ensure_automatic_assets(session, catalog["paid"]) == 0
        assert promotion.commercial_approved_at == before


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        "pause",
        "tier",
        "qualification",
        "exclusion",
        "global",
        "withdrawn",
        "unresolved",
        "connection",
    ],
)
def test_fallback_and_tracking_recheck_every_live_policy_gate(database, catalog, change):
    configure_trusted(database, catalog)
    item = fallback(database)[0]
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["paid"])
        product = session.get(PartnerProduct, catalog["paid_product"])
        listing = session.scalar(
            select(PartnerListing).where(PartnerListing.product_id == product.id)
        )
        if change == "pause":
            account.status = "paused"
        elif change == "tier":
            account.tier = "bronze"
        elif change == "qualification":
            account.approval_mode = "qualified"
        elif change == "exclusion":
            session.get(PartnerProductPromotion, (account.id, product.id)).excluded = True
        elif change == "global":
            product.excluded = True
        elif change == "withdrawn":
            listing.active = False
        elif change == "unresolved":
            listing.identity_status = "unresolved"
        elif change == "connection":
            session.get(PartnerConnection, "paid").enabled = False
    assert fallback(database) == []
    with database.begin() as session, pytest.raises(InactivePartnerAsset):
        record_delivery_event(session, PartnerTrackingTokens(KEY).verify(item["receipt"]), "click")


@pytest.mark.integration
def test_relevant_paid_match_wins_before_unrelated_fallback(database, catalog):
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["paid"])
        account.promotion_kind = None
        ensure_automatic_assets(session, account.id)
    with database.begin() as session:
        items = deliver_placements(
            session, PlacementQuery(surface="topic", topic_id=catalog["topic"]), KEY
        )
        assert (
            next(row for row in items if row["sponsored"])["product_id"] == catalog["paid_product"]
        )
        assert next(row for row in items if row["sponsored"])["campaign_id"] == catalog["campaign"]


@pytest.mark.integration
def test_standing_campaign_cap_and_exposure_exclusion_are_not_bypassed(database, catalog):
    configure_trusted(database, catalog)
    with database.begin() as session:
        campaign = session.scalar(
            select(PartnerCampaign).where(
                PartnerCampaign.account_id == catalog["paid"], PartnerCampaign.managed.is_(True)
            )
        )
        campaign.daily_impression_limit = 1
    item = fallback(database)[0]
    assert fallback(database) == []
    assert fallback(database, exclude_product_ids=[catalog["paid_product"]]) == []
    with database.begin() as session:
        assert record_delivery_event(
            session, PartnerTrackingTokens(KEY).verify(item["receipt"]), "impression"
        )
    assert fallback(database) == []


@pytest.mark.integration
def test_explicit_live_campaign_limit_cannot_be_bypassed_by_inherited_campaign(database, catalog):
    configure_trusted(database, catalog)
    with database.begin() as session:
        campaign = session.get(PartnerCampaign, catalog["campaign"])
        campaign.status = "active"
        campaign.targeting, campaign.product_ids = "selected", [str(catalog["paid_product"])]
        campaign.daily_impression_limit = 1
    # No contextual match exists, and the explicit sale owns this product even
    # when its standing tier agreement would otherwise allow a fallback.
    assert fallback(database) == []


@pytest.mark.integration
@pytest.mark.parametrize("destination", ["product_url", "listing_url"])
def test_destination_uses_account_owned_listing_and_preserves_devfeed_utm(
    database, catalog, destination
):
    configure_trusted(database, catalog, outbound_destination=destination)
    with database.begin() as session:
        listing = session.scalar(
            select(PartnerListing).where(PartnerListing.product_id == catalog["paid_product"])
        )
        listing.listing_url = "https://paid.example/item?ref=partner&utm_source=old#details"
        # A different provider owns the metadata source, which must never choose
        # the paid account's outbound listing destination.
        other = PartnerListing(
            product_id=catalog["paid_product"],
            provider="free",
            external_id="other",
            name="Other",
            product_url="https://tools.example/paid_product",
            listing_url="https://wrong.example/item",
            description="Another platform",
        )
        session.add(other)
        session.flush()
        session.get(PartnerProduct, catalog["paid_product"]).metadata_listing_id = other.id
    item = fallback(database)[0]
    parsed = urlsplit(item["url"])
    assert parsed.hostname == ("tools.example" if destination == "product_url" else "paid.example")
    assert parse_qs(parsed.query)["utm_source"] == ["devfeed"]
    if destination == "listing_url":
        assert parse_qs(parsed.query)["ref"] == ["partner"] and parsed.fragment == "details"


@pytest.mark.integration
@pytest.mark.parametrize("url", ["", "http://public.example/item", "https://127.0.0.1/item"])
def test_missing_or_unsafe_partner_destination_omits_card_without_wrong_fallback(
    database, catalog, url
):
    configure_trusted(database, catalog, outbound_destination="listing_url")
    with database.begin() as session:
        listing = session.scalar(
            select(PartnerListing).where(PartnerListing.product_id == catalog["paid_product"])
        )
        listing.listing_url = url
    assert fallback(database) == []


@pytest.mark.integration
def test_bounded_scheduler_enrolls_trusted_inventory_without_ai_qualification(database, catalog):
    with database.begin() as session:
        account = session.get(PartnerAccount, catalog["paid"])
        account.tier, account.promotion_kind, account.approval_mode = "gold", None, "trusted"
        product = session.get(PartnerProduct, catalog["paid_product"])
        product.status, product.verified_at, product.technologies, product.evidence = (
            "pending",
            None,
            [],
            [],
        )
        session.get(PartnerCampaign, catalog["campaign"]).status = "ended"
    assert sync_partner_promotions(database, limit=1) >= 1
    assert sync_partner_promotions(database, limit=200) >= 1
    assert sync_partner_promotions(database, limit=200) == 0
    assert fallback(database)


@pytest.mark.integration
def test_portal_member_can_change_only_own_active_account_destination(portal_client, database):
    client, identity = portal_client
    owned, other, _, _ = seed(database)
    path = f"/v1/partner/accounts/{owned}/preferences"
    result = client.put(path, json={"outbound_destination": "listing_url"})
    assert result.status_code == 200 and result.json()["outbound_destination"] == "listing_url"
    assert (
        client.put(
            path, json={"outbound_destination": "product_url", "tier": "diamond"}
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/v1/partner/accounts/{other}/preferences",
            json={"outbound_destination": "listing_url"},
        ).status_code
        == 404
    )
    identity.subject = "no-membership"
    assert client.put(path, json={"outbound_destination": "product_url"}).status_code == 404


@pytest.mark.integration
def test_new_campaign_and_manual_ad_require_tier_entitlement(admin_client, database):
    client, _ = admin_client
    _, bronze, _, _ = seed(database)
    root = f"/v1/admin/partner-accounts/{bronze}"
    assert client.post(f"{root}/assets", json={"name": "Ad", "kind": "ad"}).status_code == 422
    assert (
        client.post(
            f"{root}/campaigns",
            json={
                "name": "Sale",
                "starts_at": utcnow().isoformat(),
                "ends_at": (utcnow() + timedelta(days=1)).isoformat(),
            },
        ).status_code
        == 422
    )


def migration_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations/versions/0031_partner_commercial_policy.py"
    )
    spec = importlib.util.spec_from_file_location("commercial_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.integration
def test_migration_preserves_existing_sale_and_downgrade_keeps_attribution(database):
    migration = migration_module()
    with database().bind.connect() as connection:
        transaction = connection.begin()
        try:
            schema = f"commercial_{uuid.uuid4().hex}"
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            connection.execute(
                text("CREATE TABLE partner_accounts(id uuid PRIMARY KEY, tier text NOT NULL)")
            )
            connection.execute(
                text(
                    "CREATE TABLE partner_campaigns(id uuid PRIMARY KEY, account_id uuid "
                    "REFERENCES partner_accounts, status text, ends_at timestamptz)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE partner_product_promotions(account_id uuid, product_id uuid, "
                    "excluded bool DEFAULT false, PRIMARY KEY(account_id,product_id))"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE partner_assets(id uuid PRIMARY KEY, campaign_id uuid "
                    "REFERENCES partner_campaigns)"
                )
            )
            account, legacy, managed, asset = (uuid.uuid4() for _ in range(4))
            connection.execute(
                text("INSERT INTO partner_accounts VALUES(:id,'bronze')"), {"id": account}
            )
            connection.execute(
                text(
                    "INSERT INTO partner_campaigns VALUES(:id,:account,'active',now()+interval "
                    "'1 day')"
                ),
                {"id": legacy, "account": account},
            )
            connection.execute(
                text("INSERT INTO partner_assets VALUES(:id,:campaign)"),
                {"id": asset, "campaign": legacy},
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert (
                    connection.scalar(
                        text("SELECT grandfathered FROM partner_campaigns WHERE id=:id"),
                        {"id": legacy},
                    )
                    is True
                )
                assert connection.execute(
                    text(
                        "SELECT approval_mode,outbound_destination,promotion_kind FROM "
                        "partner_accounts"
                    )
                ).one() == ("qualified", "product_url", None)
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(text("UPDATE partner_accounts SET promotion_kind='ad'"))
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO "
                            "partner_product_promotions(account_id,product_id,"
                            "commercial_approved_at) "
                            "VALUES(:account,:product,now())"
                        ),
                        {"account": account, "product": uuid.uuid4()},
                    )
                connection.execute(
                    text(
                        "INSERT INTO partner_campaigns(id,account_id,status,ends_at,managed) "
                        "VALUES(:id,:account,'active',now()+interval '1 day',true)"
                    ),
                    {"id": managed, "account": account},
                )
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO "
                            "partner_campaigns(id,account_id,status,ends_at,managed) "
                            "VALUES(:id,:account,'active',now()+interval '1 day',true)"
                        ),
                        {"id": uuid.uuid4(), "account": account},
                    )
                migration.downgrade()
                assert (
                    connection.scalar(
                        text("SELECT status FROM partner_campaigns WHERE id=:id"), {"id": legacy}
                    )
                    == "active"
                )
                assert (
                    connection.scalar(
                        text("SELECT status FROM partner_campaigns WHERE id=:id"), {"id": managed}
                    )
                    == "paused"
                )
                assert (
                    connection.scalar(
                        text("SELECT campaign_id FROM partner_assets WHERE id=:id"), {"id": asset}
                    )
                    == legacy
                )
                migration.upgrade()
        finally:
            transaction.rollback()


@pytest.mark.parametrize("blocked", ["excluded", "merged_into_id"])
def test_trusted_acceptance_never_overrides_global_operator_block(blocked):
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    from devfeed_core.partner_promotions import commercially_eligible

    product = NS(excluded=False, merged_into_id=None)
    setattr(product, blocked, True if blocked == "excluded" else uuid.uuid4())
    session = Mock()
    assert not commercially_eligible(session, NS(approval_mode="trusted"), product, utcnow())
    session.scalar.assert_not_called()


@pytest.mark.parametrize(
    "mode,status,fresh,approved,source,excluded,expected",
    [
        ("qualified", "approved", True, False, None, False, True),
        ("qualified", "approved", False, True, "trusted", False, False),
        ("qualified", "rejected", False, True, "trusted", False, False),
        ("trusted", "rejected", False, True, "trusted", False, True),
        ("trusted", "pending", False, True, "trusted", False, True),
        ("trusted", "rejected", False, False, None, False, False),
        ("trusted", "rejected", False, True, None, False, False),
        ("trusted", "rejected", False, True, "trusted", True, False),
    ],
)
def test_factual_and_trusted_acceptance_are_independent_live_authorities(
    mode, status, fresh, approved, source, excluded, expected
):
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    from devfeed_core.partner_promotions import commercially_eligible

    now = utcnow()
    product = NS(
        id=uuid.uuid4(),
        excluded=False,
        merged_into_id=None,
        status=status,
        verified_at=now if fresh else None,
    )
    account = NS(id=uuid.uuid4(), approval_mode=mode)
    session = Mock()
    session.scalar.return_value = NS(
        commercial_approved_at=now if approved else None, approval_source=source, excluded=excluded
    )
    assert commercially_eligible(session, account, product, now) is expected
    if mode == "qualified" or fresh:
        session.scalar.assert_not_called()


@pytest.mark.parametrize("destination", ["product_url", "listing_url"])
def test_outbound_selection_preserves_query_and_fragment_and_overwrites_only_devfeed_source(
    destination,
):
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    from devfeed_core.partner_placements import placement_destination
    from sqlalchemy.dialects.postgresql import dialect

    account = NS(id=uuid.uuid4(), outbound_destination=destination)
    raw = "https://catalog.example/item?a=1&a=2&utm_source=old&utm_medium=referral#features"
    product = NS(id=uuid.uuid4(), product_url=raw)
    session = Mock()
    session.scalars.return_value = [NS(listing_url=raw)]
    result = urlsplit(placement_destination(session, account, product))
    assert parse_qs(result.query) == {
        "a": ["1", "2"],
        "utm_source": ["devfeed"],
        "utm_medium": ["referral"],
    }
    assert result.fragment == "features"
    if destination == "product_url":
        session.scalars.assert_not_called()
    else:
        statement = session.scalars.call_args.args[0].compile(dialect=dialect())
        assert account.id in statement.params.values() and product.id in statement.params.values()
        assert "partner_connections.enabled IS true" in str(statement)
        assert "partner_listings.active IS true" in str(statement)
        assert "ORDER BY partner_listings.provider, partner_listings.external_id" in str(statement)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",
        "https://127.0.0.1/",
        "https://localhost/",
        "https://user:pass@example.com/",
        "javascript:alert(1)",
        "",
    ],
)
def test_destination_validation_precedes_receipt_issuance(url):
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    from devfeed_core.partner_placements import placement_destination

    assert (
        placement_destination(Mock(), NS(outbound_destination="product_url"), NS(product_url=url))
        is None
    )


def test_missing_owned_listing_has_no_silent_product_url_fallback():
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    from devfeed_core.partner_placements import placement_destination

    session = Mock()
    session.scalars.return_value = []
    assert (
        placement_destination(
            session,
            NS(id=uuid.uuid4(), outbound_destination="listing_url"),
            NS(id=uuid.uuid4(), product_url="https://product.example/"),
        )
        is None
    )


@pytest.mark.parametrize(
    "tier,override", [("bronze", None), ("silver", "product"), ("gold", "product")]
)
def test_standing_paid_agreement_is_not_created_for_ordinary_defaults(tier, override):
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    from devfeed_core.partner_promotions import ensure_managed_campaign

    session = Mock()
    assert (
        ensure_managed_campaign(session, NS(tier=tier, promotion_kind=override), utcnow()) is None
    )
    session.scalar.assert_not_called()


def test_standing_agreement_creation_is_idempotent_and_does_not_impersonate_invoice_window():
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    from devfeed_core.partner_promotions import ensure_managed_campaign
    from sqlalchemy.dialects.postgresql import dialect

    session = Mock()
    account = NS(id=uuid.uuid4(), tier="gold", promotion_kind=None)
    existing = uuid.uuid4()
    session.scalar.side_effect = [None, existing]
    now = utcnow()
    assert ensure_managed_campaign(session, account, now) == existing
    statement = session.scalar.call_args_list[0].args[0].compile(dialect=dialect())
    assert statement.params["account_id"] == account.id
    assert statement.params["managed"] is True and statement.params["targeting"] == "all"
    assert statement.params["starts_at"] == now and statement.params["ends_at"].year == 9999
    assert "ON CONFLICT DO NOTHING" in str(statement)


@pytest.mark.integration
def test_partner_destination_skips_invalid_owned_listing_without_selecting_other_account(
    database, catalog
):
    configure_trusted(database, catalog, outbound_destination="listing_url")
    with database.begin() as session:
        listing = session.scalar(
            select(PartnerListing).where(PartnerListing.product_id == catalog["paid_product"])
        )
        listing.listing_url = ""
        session.add(
            PartnerConnection(
                provider="zz-owned",
                name="Second owned platform",
                enabled=True,
                account_id=catalog["paid"],
            )
        )
        session.flush()
        session.add(
            PartnerListing(
                product_id=catalog["paid_product"],
                provider="zz-owned",
                external_id="second",
                name="Same",
                product_url="https://tools.example/paid_product",
                listing_url="https://second-owned.example/item",
                description="Owned alternative",
            )
        )
    assert urlsplit(fallback(database)[0]["url"]).hostname == "second-owned.example"


@pytest.mark.integration
def test_grandfathered_existing_bronze_sale_delivers_until_its_original_end(database, catalog):
    with database.begin() as session:
        old_sale = PartnerCampaign(
            account_id=catalog["free"],
            name="Existing paid sale",
            status="active",
            grandfathered=True,
            targeting="all",
            starts_at=utcnow() - timedelta(days=1),
            ends_at=utcnow() + timedelta(days=1),
            priority=100,
        )
        session.add(old_sale)
        session.flush()
        ensure_automatic_assets(session, catalog["free"])
        sale_id = old_sale.id
    with database.begin() as session:
        items = deliver_placements(
            session, PlacementQuery(surface="article", article_id=catalog["article"]), KEY
        )
        assert any(item["campaign_id"] == sale_id for item in items)
        session.get(PartnerCampaign, sale_id).ends_at = utcnow() - timedelta(seconds=1)
    with database.begin() as session:
        assert not any(
            item["campaign_id"] == sale_id
            for item in deliver_placements(
                session, PlacementQuery(surface="article", article_id=catalog["article"]), KEY
            )
        )
