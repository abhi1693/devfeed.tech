"""Product delivery restrictions preserve inheritance and account ownership boundaries."""

import uuid

import pytest
from devfeed_core.models import PartnerAccount, PartnerMembership, PartnerProductPromotion
from devfeed_core.partner_placements import account_product_available
from devfeed_http.partner_portal import ProductPromotionInput
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_partner_portal import ISSUER
from test_partner_portal import admin_portal_client as admin_portal_client
from test_partner_portal import portal_client as portal_client
from test_partner_promotions import seed_catalog


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"excluded": None},
        {"ad_formats": ["unknown"]},
        {"ad_formats": ["feed", "feed"]},
        {"promotion_surfaces": ["feed", "feed"]},
        {"promotion_surfaces": ["unknown"]},
        {"tier": "diamond"},
    ],
)
def test_product_settings_reject_ambiguous_or_unrelated_mutations(payload):
    with pytest.raises(ValidationError):
        ProductPromotionInput(**payload)


@pytest.mark.parametrize("field", ["ad_formats", "promotion_surfaces"])
def test_explicit_inheritance_and_disabled_delivery_are_distinct(field):
    inherited = ProductPromotionInput(**{field: None})
    disabled = ProductPromotionInput(**{field: []})
    assert inherited.model_dump(exclude_unset=True) == {field: None}
    assert disabled.model_dump(exclude_unset=True) == {field: []}


def owned_catalog(database, *, tier="silver"):
    with database() as session:
        account = PartnerAccount(name="Product settings partner", tier=tier)
        session.add(account)
        session.flush()
        _, product_id = seed_catalog(session, account.id)
        session.add(PartnerMembership(account_id=account.id, issuer=ISSUER, subject="alice"))
        session.commit()
        return account.id, product_id


@pytest.mark.integration
def test_admin_and_member_edits_preserve_other_product_settings(
    database, admin_portal_client, portal_client
):
    account, product = owned_catalog(database)
    admin, _ = admin_portal_client
    portal, _ = portal_client
    admin_url = f"/v1/admin/partner-accounts/{account}/products/{product}/promotion"
    portal_url = f"/v1/partner/accounts/{account}/products/{product}/promotion"
    initial = portal.get(portal_url)
    assert initial.status_code == 200, initial.text
    assert initial.json()["ad_formats"] is None
    assert initial.json()["promotion_surfaces"] is None
    response = admin.put(
        admin_url,
        json={"ad_formats": ["engagement", "sponsored_content"], "promotion_surfaces": ["feed"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["excluded"] is False

    excluded = portal.put(portal_url, json={"excluded": True})
    assert excluded.status_code == 200, excluded.text
    assert excluded.json()["ad_formats"] == ["engagement", "sponsored_content"]
    assert excluded.json()["promotion_surfaces"] == ["feed"]
    enabled = portal.put(portal_url, json={"excluded": False, "ad_formats": None})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["ad_formats"] is None
    assert enabled.json()["promotion_surfaces"] == ["feed"]
    restored = admin.put(admin_url, json={"promotion_surfaces": None})
    assert restored.status_code == 200, restored.text
    assert restored.json()["promotion_surfaces"] is None
    assert restored.json()["excluded"] is False

    inventory = portal.get(f"/v1/partner/accounts/{account}/inventory").json()
    assert inventory["total"] == 1
    assert inventory["items"][0]["id"] == str(product)
    assert inventory["items"][0]["ad_formats"] is None
    assert inventory["items"][0]["promotion_surfaces"] is None


@pytest.mark.integration
def test_product_settings_never_grant_foreign_catalog_or_account_access(
    database, admin_portal_client, portal_client
):
    account, product = owned_catalog(database)
    with database() as session:
        foreign_account, foreign_product = seed_catalog(session, provider="foreign-catalog")
        session.commit()
    admin, _ = admin_portal_client
    portal, identity = portal_client
    for client, prefix in (
        (admin, f"/v1/admin/partner-accounts/{account}"),
        (portal, f"/v1/partner/accounts/{account}"),
    ):
        for product_id in (foreign_product, uuid.uuid4()):
            path = f"{prefix}/products/{product_id}/promotion"
            assert client.get(path).status_code == 404
            assert client.put(path, json={"ad_formats": ["feed"]}).status_code == 404
    identity.roles = ["superuser"]
    foreign_prefix = f"/v1/partner/accounts/{foreign_account}"
    assert portal.get(f"{foreign_prefix}/inventory").status_code == 404
    path = f"{foreign_prefix}/products/{product}/promotion"
    assert portal.get(path).status_code == 404
    assert portal.put(path, json={"excluded": False}).status_code == 404


@pytest.mark.integration
def test_product_formats_respect_tier_and_retain_saved_rules_on_downgrade(database, portal_client):
    account, product = owned_catalog(database)
    portal, _ = portal_client
    path = f"/v1/partner/accounts/{account}/products/{product}/promotion"
    allowed = portal.put(path, json={"ad_formats": ["engagement"]})
    assert allowed.status_code == 200, allowed.text
    with database() as session:
        session.get(PartnerAccount, account).tier = "bronze"
        session.commit()
    assert portal.get(path).json()["ad_formats"] == ["engagement"]
    assert portal.put(path, json={"excluded": True}).status_code == 200
    denied = portal.put(path, json={"ad_formats": ["header_sponsorship"]})
    assert denied.status_code == 422, denied.text
    reset = portal.put(path, json={"ad_formats": None})
    assert reset.status_code == 200, reset.text
    assert reset.json()["excluded"] is True
    assert portal.put(path, json={"ad_formats": ["feed"]}).status_code == 200


@pytest.mark.integration
def test_live_product_rules_restrict_format_and_location_without_reassigning_ownership(database):
    account, product = owned_catalog(database)
    with database() as session:
        assert account_product_available(
            session, account, product, ad_format="feed", surface="article"
        )
        policy = PartnerProductPromotion(
            account_id=account,
            product_id=product,
            excluded=False,
            ad_formats=["engagement"],
            promotion_surfaces=["feed"],
        )
        session.add(policy)
        session.flush()
        assert account_product_available(
            session, account, product, ad_format="engagement", surface="feed"
        )
        assert not account_product_available(
            session, account, product, ad_format="feed", surface="feed"
        )
        assert not account_product_available(
            session, account, product, ad_format="engagement", surface="article"
        )
        policy.ad_formats = []
        session.flush()
        assert not account_product_available(
            session, account, product, ad_format="engagement", surface="feed"
        )
        policy.ad_formats = None
        policy.promotion_surfaces = None
        session.flush()
        assert account_product_available(
            session, account, product, ad_format="feed", surface="article"
        )
        policy.excluded = True
        session.flush()
        assert not account_product_available(
            session, account, product, ad_format="feed", surface="article"
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "field,value",
    [("ad_formats", '["unknown"]'), ("promotion_surfaces", '["unknown"]')],
)
def test_database_rejects_unknown_product_delivery_rules(database, field, value):
    account, product = owned_catalog(database)
    with database() as session:
        session.add(PartnerProductPromotion(account_id=account, product_id=product))
        session.flush()
        statement = text(
            f"UPDATE partner_product_promotions SET {field}=CAST(:value AS JSONB) "
            "WHERE account_id=:account AND product_id=:product"
        )
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(statement, {"value": value, "account": account, "product": product})
