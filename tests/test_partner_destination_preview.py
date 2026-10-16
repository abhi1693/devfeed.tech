"""Real account destination previews preserve ownership and never issue placements."""

import uuid
from datetime import timedelta
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from devfeed_core.models import (
    PartnerAccount,
    PartnerConnection,
    PartnerListing,
    PartnerMembership,
    PartnerProduct,
    PartnerProductPromotion,
    utcnow,
)
from devfeed_core.partner_placement_destinations import account_product_creative
from devfeed_http.partner_portal import destination_preview
from devfeed_partner_api import auth, portal
from devfeed_partner_api.config import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_partner_admin_boundary import admin_boundary as admin_boundary_fixture
from test_partner_portal import ISSUER
from test_partner_portal import admin_portal_client as admin_client_fixture
from test_partner_portal import portal_client as portal_client_fixture

admin_boundary = admin_boundary_fixture
admin_client = admin_client_fixture
portal_client = portal_client_fixture


@pytest.fixture
def catalog(database):
    with database.begin() as session:
        account = PartnerAccount(name="Our partner", tier="gold", approval_mode="trusted")
        other = PartnerAccount(name="Other partner", tier="gold", approval_mode="trusted")
        session.add_all([account, other])
        session.flush()
        session.add_all(
            [
                PartnerConnection(provider="ours", enabled=True, account_id=account.id),
                PartnerConnection(provider="other", enabled=True, account_id=other.id),
                PartnerMembership(account_id=account.id, issuer=ISSUER, subject="alice"),
            ]
        )
        product = PartnerProduct(
            name="Actual product",
            tagline="Actual catalog description",
            product_url="https://product.example/tool?plan=pro&utm_source=old#features",
            status="approved",
            verified_at=utcnow(),
        )
        session.add(product)
        session.flush()
        listing = PartnerListing(
            provider="ours",
            external_id="tool",
            product_id=product.id,
            name=product.name,
            tagline=product.tagline,
            product_url=product.product_url,
            listing_url="https://partner.example/products/tool?ref=account&utm_source=old#pricing",
        )
        session.add(listing)
        session.flush()
        return {
            "account": account.id,
            "other": other.id,
            "product": product.id,
            "listing": listing.id,
        }


def preview_url(account_id, *, admin=False):
    prefix = "/v1/admin/partner-accounts" if admin else "/v1/partner/accounts"
    return f"{prefix}/{account_id}/destination-preview"


@pytest.mark.parametrize("roles,status", [(["admin"], 403), (["partner"], 403)])
def test_preview_requires_admin_superuser(admin_boundary, roles, status):
    client, session, record, token, _ = admin_boundary
    record["roles"] = roles
    response = client.get(
        preview_url(uuid.uuid4(), admin=True),
        headers={"Cookie": f"__Host-devfeed_admin_session={token}"},
    )
    assert response.status_code == status
    session.scalar.assert_not_called()
    session.commit.assert_not_called()


def test_preview_does_not_accept_partner_cookie_at_admin_boundary(admin_boundary):
    client, session, _, token, _ = admin_boundary
    response = client.get(
        preview_url(uuid.uuid4(), admin=True),
        headers={"Cookie": f"__Host-devfeed_partner_session={token}"},
    )
    assert response.status_code == 401
    session.scalar.assert_not_called()


def test_partner_preview_requires_authentication(monkeypatch):
    settings = Settings(
        _env_file=None,
        partner_base_url="https://partner.example",
        oidc_issuer_url=ISSUER,
        oidc_client_id="partner-client",
        oidc_organization_id="org-1",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    redis = Mock()
    monkeypatch.setattr(auth, "get_redis", lambda: redis)
    app = FastAPI()
    app.include_router(portal.router)
    session = Mock()
    from devfeed_partner_api.dependencies import get_session

    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        assert client.get(preview_url(uuid.uuid4())).status_code == 401
    session.scalar.assert_not_called()
    redis.get.assert_not_called()


@pytest.mark.integration
def test_both_apis_preview_real_product_and_identical_delivery_urls(
    portal_client, admin_client, database, catalog
):
    client, _ = portal_client
    admin, _ = admin_client
    account_id = catalog["account"]
    response = client.get(preview_url(account_id))
    assert response.status_code == 200
    result = response.json()
    assert admin.get(preview_url(account_id, admin=True)).json() == result
    product = result["product"]
    assert product == {
        "id": str(catalog["product"]),
        "name": "Actual product",
        "product_url": "https://product.example/tool?plan=pro&utm_source=devfeed#features",
        "listing_url": "https://partner.example/products/tool?ref=account&utm_source=devfeed#pricing",
    }
    for field, retained, fragment in (
        ("product_url", {"plan": ["pro"]}, "features"),
        ("listing_url", {"ref": ["account"]}, "pricing"),
    ):
        parts = urlsplit(product[field])
        assert parse_qs(parts.query) == {**retained, "utm_source": ["devfeed"]}
        assert parts.fragment == fragment
        with database.begin() as session:
            account = session.get(PartnerAccount, account_id)
            account.outbound_destination = field
            stored = session.get(PartnerProduct, catalog["product"])
            creative = account_product_creative(session, account, stored, lock=True)
            assert creative.destination(account, stored) == product[field]
    # Changing the preference never changes the catalog product chosen for preview.
    assert client.get(preview_url(account_id)).json() == result


@pytest.mark.integration
def test_partner_scope_is_live_and_cannot_be_changed_by_query_parameters(
    portal_client, database, catalog
):
    client, identity = portal_client
    own, other = catalog["account"], catalog["other"]
    assert client.get(preview_url(other)).status_code == 404
    forged = client.get(f"{preview_url(own)}?account_id={other}&subject=other")
    assert forged.status_code == 200 and forged.json()["product"]["id"] == str(catalog["product"])
    identity.issuer = "https://other-issuer.example"
    assert client.get(preview_url(own)).status_code == 404
    identity.issuer = ISSUER
    with database.begin() as session:
        session.delete(session.get(PartnerMembership, (own, ISSUER, "alice")))
    assert client.get(preview_url(own)).status_code == 404


@pytest.mark.integration
def test_paused_partner_is_hidden_but_admin_can_preview_its_configuration(
    portal_client, admin_client, database, catalog
):
    with database.begin() as session:
        session.get(PartnerAccount, catalog["account"]).status = "paused"
    assert portal_client[0].get(preview_url(catalog["account"])).status_code == 404
    response = admin_client[0].get(preview_url(catalog["account"], admin=True))
    assert response.status_code == 200 and response.json()["product"] is not None


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        "global_block",
        "account_block",
        "stale",
        "unverified",
        "rejected",
        "withdrawn",
        "unresolved",
        "disabled",
        "unowned",
    ],
)
def test_ineligible_catalog_is_not_previewed(portal_client, database, catalog, change):
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["product"])
        listing = session.get(PartnerListing, catalog["listing"])
        if change == "global_block":
            product.excluded = True
        elif change == "account_block":
            session.add(
                PartnerProductPromotion(
                    account_id=catalog["account"], product_id=product.id, excluded=True
                )
            )
        elif change == "stale":
            product.verified_at = utcnow() - timedelta(days=91)
        elif change == "unverified":
            product.verified_at = None
        elif change == "rejected":
            product.status = "rejected"
        elif change == "withdrawn":
            listing.active = False
        elif change == "unresolved":
            listing.identity_status = "unresolved"
        elif change == "disabled":
            session.get(PartnerConnection, "ours").enabled = False
        else:
            session.get(PartnerConnection, "ours").account_id = catalog["other"]
    assert portal_client[0].get(preview_url(catalog["account"])).json() == {"product": None}


@pytest.mark.integration
def test_qualified_product_precedes_trusted_inventory_and_ties_are_stable(database, catalog):
    with database.begin() as session:
        trusted = PartnerProduct(
            name="A consumer tool",
            tagline="Not qualified",
            product_url="https://consumer.example",
            status="rejected",
        )
        session.add(trusted)
        session.flush()
        session.add_all(
            [
                PartnerProductPromotion(
                    account_id=catalog["account"],
                    product_id=trusted.id,
                    commercial_approved_at=utcnow(),
                    approval_source="trusted",
                ),
                PartnerListing(
                    provider="ours",
                    external_id="trusted",
                    product_id=trusted.id,
                    name=trusted.name,
                    tagline=trusted.tagline,
                    product_url=trusted.product_url,
                    listing_url="https://partner.example/trusted",
                ),
            ]
        )
    with database() as session:
        account = session.get(PartnerAccount, catalog["account"])
        assert destination_preview(session, account).product.id == catalog["product"]
    with database.begin() as session:
        original = session.get(PartnerProduct, catalog["product"])
        original.verified_at = utcnow() - timedelta(days=91)
    with database() as session:
        account = session.get(PartnerAccount, catalog["account"])
        assert destination_preview(session, account).product.id == trusted.id
        account.approval_mode = "qualified"
        assert destination_preview(session, account).product is None
    with database.begin() as session:
        session.get(PartnerProduct, catalog["product"]).verified_at = utcnow()
        second = PartnerProduct(
            id=uuid.UUID(int=1),
            name="ACTUAL PRODUCT",
            tagline="Second",
            product_url="https://second.example",
            status="approved",
            verified_at=utcnow(),
        )
        session.add(second)
        session.flush()
        session.add(
            PartnerListing(
                provider="ours",
                external_id="second",
                product_id=second.id,
                name=second.name,
                tagline=second.tagline,
                product_url=second.product_url,
                listing_url="https://partner.example/second",
            )
        )
    with database() as session:
        account = session.get(PartnerAccount, catalog["account"])
        assert destination_preview(session, account).product.id == uuid.UUID(int=1)


@pytest.mark.integration
def test_trusted_acceptance_cannot_be_borrowed_from_another_account(database, catalog):
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["product"])
        product.status, product.verified_at = "rejected", None
        session.add(
            PartnerProductPromotion(
                account_id=catalog["other"],
                product_id=product.id,
                commercial_approved_at=utcnow(),
                approval_source="trusted",
            )
        )
    with database() as session:
        assert (
            destination_preview(session, session.get(PartnerAccount, catalog["account"])).product
            is None
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "product_url", ["http://product.example/tool", "https://127.0.0.1/tool", "javascript:alert(1)"]
)
def test_unsafe_official_url_is_never_returned(portal_client, database, catalog, product_url):
    with database.begin() as session:
        session.get(PartnerProduct, catalog["product"]).product_url = product_url
    assert portal_client[0].get(preview_url(catalog["account"])).json() == {"product": None}


@pytest.mark.integration
@pytest.mark.parametrize(
    "listing_url",
    ["", "http://partner.example/tool", "https://127.0.0.1/tool", "javascript:alert(1)"],
)
def test_missing_or_unsafe_owned_listing_stays_null_without_foreign_fallback(
    database, catalog, listing_url
):
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["product"])
        session.get(PartnerListing, catalog["listing"]).listing_url = listing_url
        foreign = PartnerListing(
            provider="other",
            external_id="foreign",
            product_id=product.id,
            name=product.name,
            tagline=product.tagline,
            product_url=product.product_url,
            listing_url="https://other.example/tool",
        )
        session.add(foreign)
        session.flush()
        product.metadata_listing_id = foreign.id
    with database() as session:
        preview = destination_preview(
            session, session.get(PartnerAccount, catalog["account"])
        ).product
        assert preview.id == catalog["product"] and preview.listing_url is None
        assert preview.product_url.startswith("https://product.example/")


@pytest.mark.integration
def test_first_usable_owned_listing_is_deterministic(database, catalog):
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["product"])
        session.get(PartnerListing, catalog["listing"]).listing_url = ""
        session.add_all(
            [
                PartnerListing(
                    provider="ours",
                    external_id=external_id,
                    product_id=product.id,
                    name=product.name,
                    tagline=product.tagline,
                    product_url=product.product_url,
                    listing_url=f"https://partner.example/{external_id}",
                )
                for external_id in ["z-valid", "v-valid"]
            ]
        )
    with database() as session:
        preview = destination_preview(
            session, session.get(PartnerAccount, catalog["account"])
        ).product
        assert preview.listing_url == "https://partner.example/v-valid?utm_source=devfeed"


@pytest.mark.integration
def test_empty_account_and_missing_account_are_distinct(admin_client, catalog):
    client, _ = admin_client
    assert client.get(preview_url(catalog["other"], admin=True)).json() == {"product": None}
    assert client.get(preview_url(uuid.uuid4(), admin=True)).status_code == 404


@pytest.mark.integration
def test_preview_is_valid_in_a_read_only_transaction_and_rechecks_current_url(database, catalog):
    with database.begin() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        preview = destination_preview(
            session, session.get(PartnerAccount, catalog["account"])
        ).product
        assert preview.product_url.startswith("https://product.example/tool?")
        assert not session.new and not session.dirty
    with database.begin() as session:
        session.get(PartnerProduct, catalog["product"]).product_url = "https://replacement.example"
    with database() as session:
        preview = destination_preview(
            session, session.get(PartnerAccount, catalog["account"])
        ).product
        assert preview.product_url == "https://replacement.example/?utm_source=devfeed"
