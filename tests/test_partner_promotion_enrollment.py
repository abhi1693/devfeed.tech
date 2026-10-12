"""Large partner catalogs enroll safely without rolling back valid account edits."""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core import partner_promotions as promotions
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerConnection,
    PartnerListing,
    PartnerMembership,
    PartnerProduct,
    PartnerProductPromotion,
    PartnerPromotionEnrollment,
    utcnow,
)
from devfeed_core.partner_promotions import ensure_automatic_assets, sync_partner_promotions
from devfeed_http.partner_portal import AccountInput, CampaignInput
from sqlalchemy import delete, event, func, insert, select, update
from test_partner_portal import admin_portal_client as admin_client_fixture
from test_partner_portal import portal_client as portal_client_fixture
from test_partner_promotions import seed_catalog

NOW = datetime.now(UTC)
admin_portal_client = admin_client_fixture
portal_client = portal_client_fixture


@pytest.fixture
def large_catalog(database):
    account_id = uuid.uuid4()
    product_ids = [uuid.UUID(int=10_000 + index) for index in range(6000)]
    with database.begin() as session:
        session.add(PartnerAccount(id=account_id, name="Large catalog", tier="platinum"))
        session.flush()
        session.add(
            PartnerConnection(provider="large-catalog", account_id=account_id, enabled=True)
        )
        session.add(
            PartnerMembership(
                account_id=account_id, issuer="https://identity.example", subject="alice"
            )
        )
        session.flush()
        # Executemany keeps fixture setup independent of enrollment's multi-row
        # insert limits and avoids a separate ORM flush for every product.
        session.execute(
            insert(PartnerProduct),
            [
                {
                    "id": product_id,
                    "name": f"Tool {index}",
                    "product_url": f"https://tool{index}.example/",
                    "description": "Verified Python tooling",
                    "technologies": ["Python"],
                    "status": "approved",
                    "verified_at": NOW,
                }
                for index, product_id in enumerate(product_ids)
            ],
        )
        session.execute(
            insert(PartnerListing),
            [
                {
                    "provider": "large-catalog",
                    "external_id": str(product_id),
                    "product_id": product_id,
                    "name": f"Tool {index}",
                    "product_url": f"https://tool{index}.example/",
                    "listing_url": f"https://catalog.example/{index}",
                    "description": "Verified listing",
                    "active": True,
                    "identity_status": "resolved",
                }
                for index, product_id in enumerate(product_ids)
            ],
        )
    return {"account": account_id, "products": product_ids}


@contextmanager
def observed_enrollment_inserts(session):
    connection = session.connection()
    bind_counts = {"partner_assets": [], "partner_product_promotions": []}

    def observe(_connection, _cursor, _statement, parameters, context, executemany):
        statement = context.compiled.statement if context.compiled else None
        if not getattr(statement, "is_insert", False) or statement.table.name not in bind_counts:
            return
        count = len(parameters)
        if (
            executemany
            and isinstance(parameters, list | tuple)
            and parameters
            and isinstance(parameters[0], dict | list | tuple)
        ):
            count = max(map(len, parameters))
        bind_counts[statement.table.name].append(count)

    event.listen(connection, "before_cursor_execute", observe)
    try:
        yield bind_counts
    finally:
        event.remove(connection, "before_cursor_execute", observe)


@pytest.mark.integration
@pytest.mark.parametrize("trusted", [False, True], ids=["qualified", "trusted"])
def test_six_thousand_products_enroll_in_bounded_inserts_and_replay_idempotently(
    database, large_catalog, trusted
):
    if trusted:
        with database.begin() as session:
            session.get(PartnerAccount, large_catalog["account"]).approval_mode = "trusted"
            session.execute(
                update(PartnerProduct)
                .where(PartnerProduct.id.in_(large_catalog["products"]))
                .values(status="pending", verified_at=None)
            )
    for expected in (6000, 0):
        with database.begin() as session:
            with observed_enrollment_inserts(session) as bindings:
                assert ensure_automatic_assets(session, large_catalog["account"], NOW) == expected
            if expected:
                assert len(bindings["partner_assets"]) >= 2
                if trusted:
                    assert len(bindings["partner_product_promotions"]) >= 2
            for binds in bindings.values():
                if binds:
                    assert max(binds) <= 16_000
        with database() as verification:
            assert (
                verification.scalar(
                    select(func.count())
                    .select_from(PartnerAsset)
                    .where(PartnerAsset.account_id == large_catalog["account"])
                )
                == 6000
            )
            if trusted:
                assert (
                    verification.scalar(
                        select(func.count())
                        .select_from(PartnerProductPromotion)
                        .where(PartnerProductPromotion.account_id == large_catalog["account"])
                    )
                    == 6000
                )
                assert verification.execute(
                    select(PartnerProduct.status, PartnerProduct.verified_at)
                    .where(PartnerProduct.id.in_(large_catalog["products"]))
                    .distinct()
                ).all() == [("pending", None)]


def asset_count(database, account_id):
    with database() as session:
        return session.scalar(
            select(func.count())
            .select_from(PartnerAsset)
            .where(PartnerAsset.account_id == account_id)
        )


def complete_enrollment(database, account_id, *, limit=200, maximum_ticks=32):
    previous = asset_count(database, account_id)
    for _ in range(maximum_ticks):
        with database() as session:
            if session.get(PartnerPromotionEnrollment, account_id) is None:
                break
        sync_partner_promotions(database, limit=limit)
        current = asset_count(database, account_id)
        assert 0 <= current - previous <= limit
        previous = current
    with database() as session:
        assert session.get(PartnerPromotionEnrollment, account_id) is None
    return previous


@pytest.mark.integration
@pytest.mark.parametrize(
    "operation", ["account", "preferences", "campaign-create", "campaign-update"]
)
def test_large_catalog_updates_commit_and_enrollment_finishes_in_bounded_background_slices(
    database, large_catalog, admin_portal_client, portal_client, operation
):
    admin, _ = admin_portal_client
    portal, _ = portal_client
    account_id = large_catalog["account"]
    campaign_id = None
    previous_assets = set()
    if operation.startswith("campaign"):
        # Existing standing assets must not hide missing explicit campaign
        # enrollment from the background lane's discovery query.
        with database.begin() as session:
            assert ensure_automatic_assets(session, account_id, NOW) == 6000
            previous_assets = set(session.scalars(select(PartnerAsset.id)))
            if operation == "campaign-update":
                campaign = PartnerCampaign(
                    account_id=account_id,
                    name="Draft agreement",
                    status="draft",
                    targeting="all",
                    starts_at=NOW - timedelta(days=1),
                    ends_at=NOW + timedelta(days=1),
                )
                session.add(campaign)
                session.flush()
                campaign_id = campaign.id
    before = asset_count(database, account_id)
    prefix = f"/v1/admin/partner-accounts/{account_id}"
    if operation == "account":
        response = admin.put(
            prefix,
            json=AccountInput(
                name="Updated large catalog",
                tier="platinum",
                outbound_destination="listing_url",
            ).model_dump(mode="json"),
        )
    elif operation == "preferences":
        response = portal.put(
            f"/v1/partner/accounts/{account_id}/preferences",
            json={"outbound_destination": "listing_url"},
        )
    else:
        payload = CampaignInput(
            name="Catalog agreement",
            status="active",
            targeting="all",
            starts_at=NOW - timedelta(days=1),
            ends_at=NOW + timedelta(days=1),
        ).model_dump(mode="json")
        response = (
            admin.post(f"{prefix}/campaigns", json=payload)
            if campaign_id is None
            else admin.put(f"{prefix}/campaigns/{campaign_id}", json=payload)
        )
        assert response.status_code == (201 if operation == "campaign-create" else 200), (
            response.text
        )
        campaign_id = uuid.UUID(response.json()["id"])
    assert response.status_code in {200, 201}, response.text
    assert asset_count(database, account_id) == before
    with database() as session:
        pending = session.get(PartnerPromotionEnrollment, account_id)
        assert pending is not None and pending.product_cursor is None
        if campaign_id is None:
            account = session.get(PartnerAccount, account_id)
            assert account.outbound_destination == "listing_url"
            if operation == "account":
                assert account.name == "Updated large catalog"
        else:
            saved = session.get(PartnerCampaign, campaign_id)
            assert saved.name == "Catalog agreement" and saved.status == "active"

    assert complete_enrollment(database, account_id) == before + 6000
    with database() as session:
        assets = set(
            session.scalars(select(PartnerAsset.id).where(PartnerAsset.account_id == account_id))
        )
        assert previous_assets <= assets
        assert not session.execute(
            select(PartnerAsset.product_id, PartnerAsset.campaign_id)
            .where(PartnerAsset.account_id == account_id)
            .group_by(PartnerAsset.product_id, PartnerAsset.campaign_id)
            .having(func.count() > 1)
        ).all()
        if campaign_id is not None:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(PartnerAsset)
                    .where(PartnerAsset.campaign_id == campaign_id)
                )
                == 6000
            )
    assert sync_partner_promotions(database) == 0
    with database() as session:
        assert (
            set(
                session.scalars(
                    select(PartnerAsset.id).where(PartnerAsset.account_id == account_id)
                )
            )
            == assets
        )


@pytest.mark.integration
def test_edit_during_enrollment_resets_cursor_and_revisits_already_processed_products(
    database, large_catalog, admin_portal_client
):
    account_id = large_catalog["account"]
    with database.begin() as session:
        promotions.request_promotion_enrollment(session, account_id)
    assert sync_partner_promotions(database, limit=200) == 200
    with database() as session:
        assert session.get(PartnerPromotionEnrollment, account_id).product_cursor is not None
    first_product = large_catalog["products"][0]
    admin, _ = admin_portal_client
    response = admin.post(
        f"/v1/admin/partner-accounts/{account_id}/campaigns",
        json=CampaignInput(
            name="New agreement for an already processed product",
            status="active",
            targeting="selected",
            product_ids=[first_product],
            starts_at=NOW - timedelta(days=1),
            ends_at=NOW + timedelta(days=1),
        ).model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    campaign_id = uuid.UUID(response.json()["id"])
    with database() as session:
        assert session.get(PartnerPromotionEnrollment, account_id).product_cursor is None
    assert sync_partner_promotions(database, limit=200) == 1
    with database() as session:
        assert (
            session.scalar(
                select(PartnerAsset.product_id).where(PartnerAsset.campaign_id == campaign_id)
            )
            == first_product
        )
    assert complete_enrollment(database, account_id) == 6001


@pytest.mark.integration
def test_failed_slice_rolls_back_assets_and_cursor_then_retries_after_backoff(
    database, large_catalog
):
    account_id = large_catalog["account"]
    with database.begin() as session:
        promotions.request_promotion_enrollment(session, account_id)
    assert sync_partner_promotions(database, limit=200) == 200
    with database() as session:
        cursor = session.get(PartnerPromotionEnrollment, account_id).product_cursor
    inserts_executed = 0

    def fail_after_asset_insert(
        _connection, _cursor, _statement, _parameters, context, _executemany
    ):
        nonlocal inserts_executed
        statement = context.compiled.statement if context.compiled else None
        if getattr(statement, "is_insert", False) and statement.table.name == "partner_assets":
            inserts_executed += 1
            raise RuntimeError("Enrollment interrupted after writing assets")

    engine = database.kw["bind"]
    event.listen(engine, "after_cursor_execute", fail_after_asset_insert)
    try:
        with pytest.raises(RuntimeError, match="Enrollment interrupted"):
            sync_partner_promotions(database, limit=200)
    finally:
        event.remove(engine, "after_cursor_execute", fail_after_asset_insert)
    assert inserts_executed == 1
    assert asset_count(database, account_id) == 200
    with database() as session:
        pending = session.get(PartnerPromotionEnrollment, account_id)
        assert pending.product_cursor == cursor
        assert pending.available_at > utcnow()
    assert sync_partner_promotions(database, limit=200) == 0
    assert sync_partner_promotions(database, limit=200, now=utcnow() + timedelta(minutes=2)) == 200
    assert asset_count(database, account_id) == 400


@pytest.mark.integration
def test_future_campaign_gets_a_due_sweep_after_small_catalog_saved(database):
    now = utcnow()
    starts = now + timedelta(hours=1)
    with database.begin() as session:
        account_id, first = seed_catalog(session, provider="future-catalog")
        _, second = seed_catalog(session, account_id, provider="future-catalog", name="Second tool")
        session.get(PartnerAccount, account_id).tier = "platinum"
        campaign = PartnerCampaign(
            account_id=account_id,
            name="Future catalog agreement",
            status="active",
            targeting="all",
            starts_at=starts,
            ends_at=starts + timedelta(days=1),
        )
        session.add(campaign)
        session.flush()
        campaign_id = campaign.id
        assert promotions.request_promotion_enrollment(session, account_id, now=now) == 2
    with database() as session:
        pending = session.get(PartnerPromotionEnrollment, account_id)
        assert pending.available_at == starts
        assert pending.product_cursor is None
    with database.begin() as session:
        session.execute(
            delete(PartnerAsset).where(
                PartnerAsset.account_id == account_id, PartnerAsset.product_id == first
            )
        )
    assert asset_count(database, account_id) == 1
    assert sync_partner_promotions(database, now=starts - timedelta(seconds=1)) == 1
    assert asset_count(database, account_id) == 2
    assert sync_partner_promotions(database, now=starts - timedelta(seconds=1)) == 0
    assert sync_partner_promotions(database, now=starts + timedelta(seconds=1)) == 2
    with database() as session:
        assert set(
            session.scalars(
                select(PartnerAsset.product_id).where(PartnerAsset.campaign_id == campaign_id)
            )
        ) == {first, second}
        assert session.get(PartnerPromotionEnrollment, account_id) is None


@pytest.mark.integration
def test_busy_account_does_not_block_another_accounts_due_enrollment(database):
    now = utcnow()
    with database.begin() as session:
        busy, _ = seed_catalog(session, provider="busy-catalog")
        ready, _ = seed_catalog(session, provider="ready-catalog")
        for account_id in (busy, ready):
            session.get(PartnerAccount, account_id).tier = "platinum"
            # Durable requests remain valid if the owned catalog shrinks before
            # a worker claims them; use that small state to exercise real locks.
            session.add(
                PartnerPromotionEnrollment(
                    account_id=account_id,
                    product_cursor=None,
                    requested_at=now,
                    updated_at=now - timedelta(minutes=1) if account_id == busy else now,
                    available_at=now,
                )
            )
    with database.begin() as editor:
        assert editor.scalar(
            select(PartnerAccount).where(PartnerAccount.id == busy).with_for_update()
        )
        assert sync_partner_promotions(database, limit=1, now=now) == 1
        assert asset_count(database, busy) == 0
        assert asset_count(database, ready) == 1
        with database() as session:
            assert session.get(PartnerPromotionEnrollment, busy) is not None
            assert session.get(PartnerPromotionEnrollment, ready) is None
    assert sync_partner_promotions(database, limit=1, now=now + timedelta(seconds=1)) == 1
    assert asset_count(database, busy) == 1


@pytest.mark.integration
def test_single_product_promotion_edit_only_enrolls_that_product_in_a_large_trusted_catalog(
    database, large_catalog, admin_portal_client
):
    account_id = large_catalog["account"]
    product_id = large_catalog["products"][0]
    with database.begin() as session:
        session.get(PartnerAccount, account_id).approval_mode = "trusted"
        product = session.get(PartnerProduct, product_id)
        product.status, product.verified_at = "pending", None
    admin, _ = admin_portal_client
    response = admin.put(
        f"/v1/admin/partner-accounts/{account_id}/products/{product_id}/promotion",
        json={"excluded": False, "promotion_surfaces": ["feed"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["promotion_surfaces"] == ["feed"]
    with database() as session:
        assets = session.scalars(
            select(PartnerAsset).where(PartnerAsset.account_id == account_id)
        ).all()
        assert [asset.product_id for asset in assets] == [product_id]
        approvals = session.scalars(
            select(PartnerProductPromotion).where(PartnerProductPromotion.account_id == account_id)
        ).all()
        assert [approval.product_id for approval in approvals] == [product_id]
        assert approvals[0].commercial_approved_at is not None
        assert approvals[0].approval_source == "trusted"
        assert approvals[0].promotion_surfaces == ["feed"]
        assert not approvals[0].excluded
        assert session.get(PartnerPromotionEnrollment, account_id) is None
        assert session.get(PartnerProduct, product_id).status == "pending"
