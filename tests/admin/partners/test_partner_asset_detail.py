"""Asset editing resolves account-owned records independently of paginated reports."""

import uuid

import pytest
from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import get_session
from devfeed_admin_api.partner_accounts import router
from devfeed_core.models import PartnerAccount, PartnerAsset
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event

from tests.partners.promotions.test_promotions import seed_catalog
from tests.support.partner_portal import admin_portal_client as admin_portal_client
from tests.support.partner_portal import seed


def test_asset_detail_requires_authentication_before_database_access():
    app = FastAPI()
    app.include_router(router)

    def unauthenticated():
        raise HTTPException(401, "Admin session required")

    def forbidden_database():
        pytest.fail("Unauthenticated asset requests must not reach the database")

    app.dependency_overrides[require_admin] = unauthenticated
    app.dependency_overrides[get_session] = forbidden_database
    with TestClient(app) as client:
        response = client.get(f"/v1/admin/partner-accounts/{uuid.uuid4()}/assets/{uuid.uuid4()}")
    assert response.status_code == 401
    assert response.json() == {"detail": "Admin session required"}


@pytest.mark.integration
@pytest.mark.parametrize("roles", [[], ["admin"], ["partner"]])
def test_asset_detail_is_superuser_only(admin_portal_client, database, roles):
    client, identity = admin_portal_client
    account_id, _, asset_id, _ = seed(database)
    identity.roles = roles
    response = client.get(f"/v1/admin/partner-accounts/{account_id}/assets/{asset_id}")
    assert response.status_code == 403
    assert response.json() == {"detail": "Superuser access required"}


@pytest.mark.integration
def test_asset_detail_hides_foreign_and_missing_records(admin_portal_client, database):
    client, _ = admin_portal_client
    account_id, foreign_account, _, foreign_asset = seed(database)
    foreign = client.get(f"/v1/admin/partner-accounts/{account_id}/assets/{foreign_asset}")
    missing = client.get(f"/v1/admin/partner-accounts/{account_id}/assets/{uuid.uuid4()}")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": "Partner asset not found"}
    own = client.get(f"/v1/admin/partner-accounts/{foreign_account}/assets/{foreign_asset}")
    assert own.status_code == 200
    assert own.json()["id"] == str(foreign_asset)
    missing_account = client.get(
        f"/v1/admin/partner-accounts/{uuid.uuid4()}/assets/{foreign_asset}"
    )
    assert missing_account.status_code == 404
    assert missing_account.json() == {"detail": "Partner account not found"}


@pytest.mark.integration
def test_asset_detail_is_read_only_and_independent_of_report_pages(admin_portal_client, database):
    client, _ = admin_portal_client
    with database.begin() as session:
        account = PartnerAccount(name="Large account", tier="gold")
        session.add(account)
        session.flush()
        assets = [
            PartnerAsset(account_id=account.id, name=f"Asset {index:03}", kind="ad")
            for index in range(101)
        ]
        session.add_all(assets)
        session.flush()
        account_id, asset_id = account.id, assets[-1].id

    report_url = f"/v1/admin/partner-accounts/{account_id}/dashboard"
    first_page = client.get(report_url)
    assert first_page.status_code == 200
    report = first_page.json()
    assert report["asset_total"] == 101 and len(report["assets"]) == 100
    assert str(asset_id) not in {row["id"] for row in report["assets"]}

    statements = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = database.kw["bind"]
    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        detail = client.get(
            f"/v1/admin/partner-accounts/{str(account_id).upper()}/assets/{str(asset_id).upper()}"
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
    assert detail.status_code == 200
    assert detail.json() == {
        "id": str(asset_id),
        "name": "Asset 100",
        "kind": "ad",
        "product_id": None,
        "status": "draft",
        "automatic": False,
        "campaign_id": None,
        "ad_format": "feed",
        "article_id": None,
    }
    # A bookmarked edit needs two bounded lookups, without locks, writes or report scans.
    assert len(statements) == 2
    assert all(statement.lstrip().upper().startswith("SELECT ") for statement in statements)
    assert all("FOR UPDATE" not in statement.upper() for statement in statements)

    with database.begin() as session:
        session.get(PartnerAsset, asset_id).name = "000 Reordered asset"
    later_page = client.get(f"{report_url}?offset=100").json()
    assert len(later_page["assets"]) == 1
    assert str(asset_id) not in {row["id"] for row in later_page["assets"]}
    refreshed = client.get(f"/v1/admin/partner-accounts/{account_id}/assets/{asset_id}")
    assert refreshed.status_code == 200
    assert refreshed.json()["name"] == "000 Reordered asset"


@pytest.mark.integration
def test_asset_detail_returns_the_record_instead_of_a_product_aggregate(
    admin_portal_client, database
):
    client, _ = admin_portal_client
    with database.begin() as session:
        account = PartnerAccount(name="Product account", tier="gold")
        session.add(account)
        session.flush()
        account_id, product_id = seed_catalog(session, account.id)
        automatic = PartnerAsset(
            account_id=account_id,
            product_id=product_id,
            name="Inherited product placement",
            kind="product",
            status="active",
            automatic=True,
        )
        manual = PartnerAsset(
            account_id=account_id,
            product_id=product_id,
            name="Manual campaign creative",
            kind="ad",
            status="paused",
            ad_format="engagement",
        )
        session.add_all([automatic, manual])
        session.flush()
        automatic_id, manual_id = automatic.id, manual.id

    report = client.get(f"/v1/admin/partner-accounts/{account_id}/dashboard").json()
    assert len(report["assets"]) == 1
    assert report["assets"][0]["aggregated"] is True
    assert report["assets"][0]["name"] == "Kube Inspect"
    for asset_id, name, status, automatic, ad_format in [
        (automatic_id, "Inherited product placement", "active", True, "feed"),
        (manual_id, "Manual campaign creative", "paused", False, "engagement"),
    ]:
        response = client.get(f"/v1/admin/partner-accounts/{account_id}/assets/{asset_id}")
        assert response.status_code == 200
        detail = response.json()
        assert detail["id"] == str(asset_id)
        assert detail["product_id"] == str(product_id)
        assert detail["name"] == name
        assert detail["status"] == status
        assert detail["automatic"] is automatic
        assert detail["ad_format"] == ad_format
        assert "aggregated" not in detail
        assert "impressions" not in detail


@pytest.mark.integration
@pytest.mark.parametrize("parameter", ["account_id", "asset_id"])
def test_asset_detail_validates_uuid_parameters(admin_portal_client, database, parameter):
    client, _ = admin_portal_client
    account_id, _, asset_id, _ = seed(database)
    identifiers = {"account_id": str(account_id), "asset_id": str(asset_id)}
    identifiers[parameter] = "not-a-uuid"
    response = client.get(
        f"/v1/admin/partner-accounts/{identifiers['account_id']}/assets/{identifiers['asset_id']}"
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["path", parameter]
