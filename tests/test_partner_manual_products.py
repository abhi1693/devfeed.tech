"""Connection-free inventory retains qualification, delivery and tenant boundaries."""

import uuid

import pytest
from devfeed_aggregator.partner_sync_tasks import process_pipeline, schedule_partner_syncs
from devfeed_core.config import get_settings
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerConnection,
    PartnerListing,
    PartnerManualProduct,
    PartnerMembership,
    PartnerPipelineJob,
    PartnerProduct,
    PartnerProductURL,
)
from devfeed_core.partner_catalog import merge_products, upsert_listing
from devfeed_core.partner_connections import configure_connection
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_core.partner_tools import ProductInput
from devfeed_core.partner_tracking import (
    InactivePartnerAsset,
    PartnerTrackingTokens,
    record_delivery_event,
)
from sqlalchemy import delete, func, select
from test_avatar_uploads import FakeR2, picture
from test_partner_placements_integration import KEY, deliver, query
from test_partner_placements_integration import catalog as catalog
from test_partner_portal import admin_portal_client as admin_portal_client
from test_partner_portal import portal_client as portal_client
from test_partner_product_ad_images import managed_storage as managed_storage

pytestmark = pytest.mark.integration
FACTS = {
    "name": "Python Observer",
    "product_url": "https://observer.example/",
    "tagline": "Inspect Python runtime metrics and application logs.",
    "pricing": "paid",
}
PAGE = {
    "final_url": FACTS["product_url"],
    "text": "Inspect Python runtime metrics and application logs.",
}
ANSWER = {
    "decision": "qualified",
    "reason": "Inspects Python runtime metrics.",
    "technologies": ["Python"],
    "evidence": [
        {
            "url": PAGE["final_url"],
            "quote": PAGE["text"],
            "capability": "Inspect Python runtime metrics",
        }
    ],
}


def seed_account(database, identity, **values):
    with database.begin() as session:
        account = PartnerAccount(name="Independent business", tier="gold", **values)
        session.add(account)
        session.flush()
        session.add(
            PartnerMembership(
                account_id=account.id, issuer=identity.issuer, subject=identity.subject
            )
        )
        return account.id


def create(client, account_id, **values):
    response = client.post(f"/v1/partner/accounts/{account_id}/products", json={**FACTS, **values})
    assert response.status_code == 201, response.text
    return response.json()


def qualification_id(database, product_id):
    with database() as session:
        return str(
            session.scalar(
                select(PartnerPipelineJob.id).where(
                    PartnerPipelineJob.product_id == uuid.UUID(str(product_id)),
                    PartnerPipelineJob.operation == "assess",
                    PartnerPipelineJob.status == "queued",
                )
            )
        )


def test_partner_can_create_edit_archive_restore_without_any_connection(portal_client, database):
    client, identity = portal_client
    account_id = seed_account(database, identity)
    row = create(client, account_id)
    base = f"/v1/partner/accounts/{account_id}/products/{row['id']}"
    assert row["source"] == "manual" and row["editable"] and row["active"]
    assert row["status"] == "pending" and not row["commercial_approved"]
    assert row["technologies"] == [] and row["assessment"]["state"] == "checking"
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerConnection)) == 0
        job = session.scalar(select(PartnerPipelineJob))
        assert job.operation == "assess" and job.provider is None
    listing = client.get(f"/v1/partner/accounts/{account_id}/inventory").json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == row["id"]
    changed = client.patch(base, json={"name": "Python Runtime Observer", "expected_revision": 1})
    assert changed.status_code == 200, changed.text
    assert changed.json()["name"] == "Python Runtime Observer" and changed.json()["revision"] == 2
    assert (
        client.patch(base, json={"name": "Stale name", "expected_revision": 1}).status_code == 409
    )
    assert client.delete(base).status_code == 204
    archived = client.get(base).json()
    assert not archived["active"] and archived["status"] == "withdrawn"
    assert client.get(f"/v1/partner/accounts/{account_id}/inventory").json()["total"] == 0
    repeated = create(client, account_id)
    assert repeated["id"] == row["id"] and not repeated["active"]
    restored = client.patch(base, json={"active": True})
    assert restored.status_code == 200 and restored.json()["active"]


def test_manual_taglines_are_bounded_plain_copy_in_detail_and_inventory(portal_client, database):
    client, identity = portal_client
    account_id = seed_account(database, identity)
    url = f"/v1/partner/accounts/{account_id}/products"
    for invalid in ("AI", "x" * 161):
        response = client.post(url, json={**FACTS, "tagline": invalid})
        assert response.status_code == 422, response.text
    row = create(client, account_id, tagline="  **Build** C++\nAPIs.  ")
    assert row["tagline"] == "Build C++ APIs." and "description" not in row
    inventory = client.get(f"/v1/partner/accounts/{account_id}/inventory").json()
    assert inventory["total"] == 1
    assert inventory["items"][0]["tagline"] == row["tagline"]
    assert "description" not in inventory["items"][0]
    base = f"{url}/{row['id']}"
    response = client.patch(base, json={"tagline": "x" * 161, "expected_revision": 1})
    assert response.status_code == 422, response.text
    assert client.get(base).json()["revision"] == 1
    response = client.patch(base, json={"tagline": "AI IDE", "expected_revision": 1})
    assert response.status_code == 200, response.text
    assert response.json()["tagline"] == "AI IDE" and response.json()["revision"] == 2
    with database() as session:
        assert session.get(PartnerProduct, uuid.UUID(row["id"])).tagline == "AI IDE"


def test_manual_product_qualification_uses_website_not_a_provider(
    portal_client, database, monkeypatch
):
    client, identity = portal_client
    account_id = seed_account(database, identity)
    row = create(client, account_id)
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    process_pipeline(
        qualification_id(database, row["id"]),
        factory=database,
        page_fetcher=lambda *_: PAGE,
        assessor=lambda *_: ANSWER,
    )
    detail = client.get(f"/v1/partner/accounts/{account_id}/products/{row['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "approved" and detail.json()["technologies"] == ["Python"]
    assert detail.json()["commercial_approved"]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerAsset)) >= 1
        assert session.scalar(select(PartnerProduct)).metadata_account_id == account_id
        assert session.scalar(select(PartnerPipelineJob)).status == "succeeded"


@pytest.mark.parametrize("mutation", ["archive", "edit"])
def test_inflight_manual_qualification_discards_archived_or_edited_results(
    portal_client, database, monkeypatch, mutation
):
    client, identity = portal_client
    account_id = seed_account(database, identity)
    row = create(client, account_id)
    base = f"/v1/partner/accounts/{account_id}/products/{row['id']}"
    old_job = qualification_id(database, row["id"])
    monkeypatch.setattr(get_settings(), "ai_enabled", True)

    def assess(*_):
        if mutation == "archive":
            assert client.delete(base).status_code == 204
        else:
            assert (
                client.patch(
                    base, json={"name": "Updated observer", "expected_revision": 1}
                ).status_code
                == 200
            )
        return ANSWER

    process_pipeline(old_job, factory=database, page_fetcher=lambda *_: PAGE, assessor=assess)
    with database() as session:
        product = session.get(PartnerProduct, uuid.UUID(row["id"]))
        assert product.status != "approved" and product.verified_at is None
        assert session.get(PartnerPipelineJob, uuid.UUID(old_job)).status == "failed"


def test_foreign_membership_cannot_create_read_edit_or_archive(portal_client, database):
    client, identity = portal_client
    account_id = seed_account(database, identity)
    row = create(client, account_id)
    identity.subject = "unrelated-user"
    base = f"/v1/partner/accounts/{account_id}/products"
    assert client.post(base, json=FACTS).status_code == 404
    assert client.get(base + f"/{row['id']}").status_code == 404
    assert client.patch(base + f"/{row['id']}", json={"active": False}).status_code == 404
    assert client.delete(base + f"/{row['id']}").status_code == 404


def test_existing_canonical_url_is_readonly_and_never_overwrites_or_clears_exclusion(
    portal_client, database
):
    client, identity = portal_client
    account_a = seed_account(database, identity)
    account_b = seed_account(database, identity)
    first = create(client, account_a)
    with database.begin() as session:
        product = session.get(PartnerProduct, uuid.UUID(first["id"]))
        product.excluded, product.status = True, "paused"
    linked = create(client, account_b, name="Hijacked name")
    assert linked["id"] == first["id"] and linked["name"] == FACTS["name"]
    assert linked["source"] == "manual" and not linked["editable"] and linked["globally_excluded"]
    base = f"/v1/partner/accounts/{account_b}/products/{linked['id']}"
    assert (
        client.patch(
            base, json={"name": "Hijacked", "expected_revision": linked["revision"]}
        ).status_code
        == 409
    )
    assert client.delete(base).status_code == 204
    assert client.get(f"/v1/partner/accounts/{account_a}/products/{first['id']}").json()["active"]


def test_duplicate_create_does_not_duplicate_aliases_or_assessment_jobs(portal_client, database):
    client, identity = portal_client
    account_id = seed_account(database, identity)
    original = create(client, account_id)
    repeated = create(client, account_id, product_url=FACTS["product_url"] + "?utm_source=other")
    assert original["id"] == repeated["id"]
    with database() as session:
        for model in (PartnerProduct, PartnerProductURL, PartnerManualProduct, PartnerPipelineJob):
            assert session.scalar(select(func.count()).select_from(model)) == 1


def test_url_change_cannot_collide_with_another_product(portal_client, database):
    client, identity = portal_client
    account_id = seed_account(database, identity)
    first = create(client, account_id)
    second = create(client, account_id, product_url="https://second.example/")
    response = client.patch(
        f"/v1/partner/accounts/{account_id}/products/{first['id']}",
        json={
            "product_url": second["product_url"],
            "expected_revision": first["revision"],
        },
    )
    assert response.status_code == 409
    with database() as session:
        assert (
            session.get(PartnerProduct, uuid.UUID(first["id"])).product_url == FACTS["product_url"]
        )


def test_imports_cannot_take_manual_metadata_authority(portal_client, database):
    client, identity = portal_client
    account_id = seed_account(database, identity)
    created = create(client, account_id)
    with database.begin() as session:
        session.add(PartnerConnection(provider="platform", enabled=True, account_id=account_id))
        session.flush()
        result = upsert_listing(
            session,
            ProductInput(
                **{**FACTS, "name": "Platform title"},
                provider="platform",
                external_id="observer",
                listing_url="https://launch.example/observer",
            ),
            uuid.uuid4(),
        )
        assert str(result.id) == created["id"] and result.name == FACTS["name"]
        assert result.metadata_listing_id is None and result.metadata_account_id == account_id
    assert client.get(f"/v1/partner/accounts/{account_id}/inventory").json()["total"] == 1


def test_pausing_import_does_not_cancel_manual_product_qualification(portal_client, database):
    client, identity = portal_client
    account_id = seed_account(database, identity)
    created = create(client, account_id)
    with database.begin() as session:
        connection = PartnerConnection(provider="platform", enabled=True, account_id=account_id)
        session.add(connection)
        session.flush()
        upsert_listing(
            session,
            ProductInput(
                **FACTS,
                provider="platform",
                external_id="observer",
                listing_url="https://launch.example/observer",
            ),
            uuid.uuid4(),
        )
        job = session.scalar(
            select(PartnerPipelineJob).where(
                PartnerPipelineJob.product_id == uuid.UUID(created["id"])
            )
        )
        job.provider = "platform"
        configure_connection(session, connection, False)
        assert job.status == "queued"


def test_archived_manual_registration_stays_available_through_active_import(
    portal_client, database
):
    client, identity = portal_client
    account_id = seed_account(database, identity)
    row = create(client, account_id)
    with database.begin() as session:
        session.add(PartnerConnection(provider="platform", enabled=True, account_id=account_id))
        session.flush()
        upsert_listing(
            session,
            ProductInput(
                **FACTS,
                provider="platform",
                external_id="observer",
                listing_url="https://launch.example/observer",
            ),
            uuid.uuid4(),
        )
    base = f"/v1/partner/accounts/{account_id}/products/{row['id']}"
    assert client.delete(base).status_code == 204
    detail = client.get(base).json()
    assert detail["active"] and detail["manual_active"] is False
    inventory = client.get(f"/v1/partner/accounts/{account_id}/inventory").json()["items"][0]
    assert inventory["active"] and inventory["manual_active"] is False
    inclusive = client.get(
        f"/v1/partner/accounts/{account_id}/inventory", params={"include_archived": "true"}
    ).json()
    assert inclusive["total"] == 1 and inclusive["items"][0]["active"]
    restored = client.patch(base, json={"active": True}).json()
    assert restored["active"] and restored["manual_active"]


def test_manual_merge_preserves_inventory_and_existing_archive_policy(portal_client, database):
    client, identity = portal_client
    account_id = seed_account(database, identity)
    first = create(client, account_id)
    second = create(client, account_id, product_url="https://second.example/")
    assert (
        client.delete(f"/v1/partner/accounts/{account_id}/products/{second['id']}").status_code
        == 204
    )
    with database.begin() as session:
        donor, recipient = (
            session.get(PartnerProduct, uuid.UUID(row["id"])) for row in (first, second)
        )
        merge_products(session, donor, recipient)
        assert not session.get(PartnerManualProduct, (account_id, recipient.id)).active
        assert recipient.metadata_account_id == account_id
    assert client.get(f"/v1/partner/accounts/{account_id}/inventory").json()["total"] == 0


def test_admin_can_manage_connection_free_products(admin_portal_client, database):
    client, identity = admin_portal_client
    account_id = seed_account(database, identity)
    base = f"/v1/admin/partner-accounts/{account_id}/products"
    response = client.post(base, json=FACTS)
    assert response.status_code == 201, response.text
    row = response.json()
    assert client.get(base + "/" + row["id"]).json()["editable"]
    assert (
        client.patch(
            base + "/" + row["id"], json={"name": "Edited", "expected_revision": 1}
        ).status_code
        == 200
    )
    assert client.delete(base + "/" + row["id"]).status_code == 204


def test_manual_qualified_products_deliver_track_and_stop_on_archive(database, catalog):
    with database.begin() as session:
        for account, key in (("free", "free_product"), ("paid", "paid_product")):
            session.add(PartnerManualProduct(account_id=catalog[account], product_id=catalog[key]))
            session.get(PartnerAccount, catalog[account]).outbound_destination = "listing_url"
        session.flush()
        session.execute(delete(PartnerListing))
        session.execute(delete(PartnerConnection))
        ensure_automatic_assets(session)
    items = deliver(database, query(catalog))
    assert {item["name"] for item in items} == {"free_product", "paid_product"}
    assert all(item["url"].startswith("https://tools.example/") for item in items)
    paid = next(item for item in items if item["name"] == "paid_product")
    tokens = PartnerTrackingTokens(KEY)
    with database.begin() as session:
        record_delivery_event(session, tokens.verify(paid["receipt"]), "impression")
    with database.begin() as session:
        session.get(PartnerManualProduct, (catalog["paid"], catalog["paid_product"])).active = False
    with database.begin() as session, pytest.raises(InactivePartnerAsset):
        record_delivery_event(session, tokens.verify(paid["receipt"]), "click")
    assert all(item["name"] != "paid_product" for item in deliver(database, query(catalog)))


def test_manual_product_scheduler_repairs_missing_assessment_without_connection(
    portal_client, database
):
    client, identity = portal_client
    account_id = seed_account(database, identity)
    created = create(client, account_id)
    with database.begin() as session:
        session.execute(delete(PartnerPipelineJob))
    schedule_partner_syncs(database)
    with database() as session:
        job = session.scalar(select(PartnerPipelineJob))
        assert job.provider is None and str(job.product_id) == created["id"]


def test_pending_manual_product_accepts_custom_image_upload_and_url(
    portal_client, database, managed_storage, monkeypatch
):
    from devfeed_core.object_storage import R2Storage
    from devfeed_http import image_uploads

    client, identity = portal_client
    account_id = seed_account(database, identity)
    row = create(client, account_id)
    r2 = FakeR2()
    monkeypatch.setattr(
        image_uploads, "image_upload_storage", lambda: R2Storage("manual-test-images", client=r2)
    )
    base = f"/v1/partner/accounts/{account_id}/products/{row['id']}"
    uploaded = client.post(
        base + "/ad-image", files={"file": ("creative.png", picture(), "image/png")}
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["ad_image_status"] == "ready" and r2.objects
    custom = client.put(
        base + "/promotion", json={"ad_image_url": "https://images.example/custom.png"}
    )
    assert custom.status_code == 200, custom.text
    assert custom.json()["ad_image_status"] == "queued"
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerConnection)) == 0
        assert session.get(PartnerProduct, uuid.UUID(row["id"])).status == "pending"


@pytest.mark.parametrize("scope", ["account", "workspace", "admin"])
def test_archived_inventory_opt_in_retains_scope_filters_counts_and_restore(
    portal_client, admin_portal_client, database, scope
):
    portal, identity = portal_client
    admin, _ = admin_portal_client
    account_id = seed_account(database, identity)
    rows = [
        create(portal, account_id, name=name, product_url=f"https://{name.lower()}.example/")
        for name in ("Alpha", "Beta", "Gamma")
    ]
    for row in rows[:2]:
        assert (
            portal.delete(f"/v1/partner/accounts/{account_id}/products/{row['id']}").status_code
            == 204
        )
    hidden = []
    for mode in ("foreign", "paused"):
        other_id = seed_account(database, identity)
        other = create(portal, other_id, product_url=f"https://{mode}.example/")
        hidden.append((other_id, other["id"]))
        assert (
            portal.delete(f"/v1/partner/accounts/{other_id}/products/{other['id']}").status_code
            == 204
        )
        with database.begin() as session:
            if mode == "foreign":
                session.delete(
                    session.get(PartnerMembership, (other_id, identity.issuer, identity.subject))
                )
            else:
                session.get(PartnerAccount, other_id).status = "paused"
    path = (
        f"/v1/admin/partner-accounts/{account_id}/inventory"
        if scope == "admin"
        else "/v1/partner/inventory"
        if scope == "workspace"
        else f"/v1/partner/accounts/{account_id}/inventory"
    )
    client = admin if scope == "admin" else portal
    default = client.get(path).json()
    assert default["total"] == 1 and default["items"][0]["name"] == "Gamma"
    all_rows = client.get(path, params={"include_archived": "true"}).json()
    assert all_rows["total"] == 3
    assert [(row["name"], row["active"], row["manual_active"]) for row in all_rows["items"]] == [
        ("Alpha", False, False),
        ("Beta", False, False),
        ("Gamma", True, True),
    ]
    page = client.get(path, params={"include_archived": "true", "limit": 1, "offset": 1}).json()
    assert page["total"] == 3 and [row["name"] for row in page["items"]] == ["Beta"]
    filtered = client.get(path, params={"include_archived": "true", "q": "Alpha"}).json()
    assert filtered["total"] == 1 and filtered["items"][0]["id"] == rows[0]["id"]
    exact = client.get(
        path, params={"include_archived": "true", "product_id": rows[1]["id"]}
    ).json()
    assert exact["total"] == 1 and exact["items"][0]["name"] == "Beta"
    for other_id, product_id in hidden:
        missing = client.get(
            path, params={"include_archived": "true", "product_id": product_id}
        ).json()
        assert missing["total"] == 0 and missing["items"] == []
        if scope == "workspace":
            assert (
                client.get(
                    path, params={"include_archived": "true", "account_id": str(other_id)}
                ).status_code
                == 404
            )
    restored = portal.patch(
        f"/v1/partner/accounts/{account_id}/products/{rows[0]['id']}", json={"active": True}
    )
    assert restored.status_code == 200
    assert client.get(path).json()["total"] == 2
