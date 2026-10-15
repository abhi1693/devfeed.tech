"""Commercial defaults inherit automatically while catalog and paid scope stay separate."""

import uuid
from datetime import UTC, datetime, timedelta

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
from devfeed_core.partner_promotions import (
    campaign_is_live,
    campaign_matches_product,
    ensure_automatic_assets,
)
from devfeed_http.partner_portal import AccountInput, CampaignInput
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

NOW = datetime.now(UTC)


def agreement(**overrides):
    return {
        "name": "Kubernetes launch",
        "status": "active",
        "starts_at": NOW - timedelta(days=1),
        "ends_at": NOW + timedelta(days=1),
        **overrides,
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"targeting": "selected"},
        {"targeting": "filters"},
        {"targeting": "all", "product_ids": [uuid.uuid4()]},
        {"targeting": "selected", "product_ids": [uuid.uuid4()], "technologies": ["Kubernetes"]},
        {"targeting": "filters", "technologies": [" "]},
        {"ends_at": NOW - timedelta(days=2)},
        {"starts_at": "2026-10-11T12:00:00"},
        {"surfaces": ["article", "article"]},
        {"daily_impression_limit": 0},
        {"priority": 101},
    ],
)
def test_campaign_requires_finite_explicit_paid_scope(fields):
    with pytest.raises(ValidationError):
        CampaignInput(**agreement(**fields))


def test_account_defaults_allow_free_recommendations_and_do_not_imply_payment():
    payload = AccountInput(name="Free partner", tier="bronze")
    assert payload.organic_enabled and payload.auto_include_products
    assert payload.promotion_surfaces == ["article", "topic", "feed", "search"]
    assert "frequency_cap" not in payload.model_dump()
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AccountInput(name="Free partner", tier="bronze", frequency_cap=3)


def test_campaign_relevance_filters_and_time_boundaries():
    product = PartnerProduct(id=uuid.uuid4(), technologies=["Kubernetes", "Python"])
    topic = uuid.uuid4()
    campaign = PartnerCampaign(
        **agreement(), targeting="filters", technologies=["kubernetes"], topic_ids=[str(topic)]
    )
    assert campaign_matches_product(campaign, product, [topic])
    assert not campaign_matches_product(campaign, product, [uuid.uuid4()])
    campaign.technologies = ["React"]
    assert not campaign_matches_product(campaign, product, [topic])
    campaign.targeting = "selected"
    campaign.product_ids = [str(product.id)]
    assert campaign_matches_product(campaign, product)
    assert campaign_is_live(campaign, NOW)
    assert not campaign_is_live(campaign, campaign.ends_at)
    campaign.status = "paused"
    assert not campaign_is_live(campaign, NOW)


def seed_catalog(session, account_id=None, provider="partner-one", name="Kube Inspect"):
    if account_id is None:
        account = PartnerAccount(name="Free catalog", tier="bronze")
        session.add(account)
        session.flush()
        account_id = account.id
    connection = session.get(PartnerConnection, provider)
    if connection is None:
        session.add(PartnerConnection(provider=provider, enabled=True, account_id=account_id))
    product = PartnerProduct(
        name=name,
        product_url=f"https://{uuid.uuid4().hex}.example",
        tagline="Kubernetes troubleshooting",
        technologies=["Kubernetes"],
        status="approved",
        verified_at=NOW,
    )
    session.add(product)
    session.flush()
    session.add(
        PartnerListing(
            product_id=product.id,
            provider=provider,
            external_id=str(product.id),
            name=name,
            product_url=product.product_url,
            listing_url=product.product_url,
            tagline=product.tagline,
            active=True,
            identity_status="resolved",
        )
    )
    session.flush()
    return account_id, product.id


@pytest.mark.integration
def test_inherited_organic_and_paid_catalog_include_future_products_idempotently(database):
    with database() as session:
        account_id, product_id = seed_catalog(session)
        account = session.get(PartnerAccount, account_id)
        account.tier, account.promotion_kind = "silver", "product"
        campaign = PartnerCampaign(account_id=account_id, **agreement(), targeting="all")
        session.add(campaign)
        session.flush()
        assert ensure_automatic_assets(session, account_id, NOW) == 2
        assert ensure_automatic_assets(session, account_id, NOW) == 0
        _, next_product = seed_catalog(session, account_id)
        assert ensure_automatic_assets(session, account_id, NOW) == 2
        rows = session.scalars(
            select(PartnerAsset).where(PartnerAsset.account_id == account_id)
        ).all()
        assert len(rows) == 4
        assert {row.product_id for row in rows} == {product_id, next_product}
        assert {row.kind for row in rows} == {"product", "ad"}
        # Expiration preserves paid attribution for historical reports.
        campaign.ends_at = NOW - timedelta(hours=1)
        assert ensure_automatic_assets(session, account_id, NOW) == 0
        assert len(session.scalars(select(PartnerAsset)).all()) == 4
        session.commit()


@pytest.mark.integration
def test_only_owned_active_resolved_catalog_associates_and_exclusions_apply(database):
    with database() as session:
        account_id, product_id = seed_catalog(session)
        _, second_product = seed_catalog(session, account_id, name="Second product")
        session.add(
            PartnerProductPromotion(account_id=account_id, product_id=product_id, excluded=True)
        )
        session.flush()
        assert ensure_automatic_assets(session, account_id, NOW) == 1
        assert session.scalar(select(PartnerAsset.product_id)) == second_product
        listing = session.scalar(
            select(PartnerListing).where(PartnerListing.product_id == second_product)
        )
        listing.identity_status = "unresolved"
        session.flush()
        assert ensure_automatic_assets(session, account_id, NOW) == 0
        account = session.get(PartnerAccount, account_id)
        account.status = "paused"
        assert ensure_automatic_assets(session, account_id, NOW) == 0


@pytest.mark.integration
def test_selected_paid_products_can_opt_in_with_catalog_automatic_inclusion_off(database):
    with database() as session:
        account_id, product_id = seed_catalog(session)
        account = session.get(PartnerAccount, account_id)
        account.tier, account.promotion_kind = "silver", "product"
        account.auto_include_products = False
        session.add(PartnerCampaign(account_id=account_id, **agreement(), targeting="all"))
        selected = PartnerCampaign(
            account_id=account_id,
            **agreement(),
            targeting="selected",
            product_ids=[str(product_id)],
        )
        session.add(selected)
        session.flush()
        assert ensure_automatic_assets(session, account_id, NOW) == 1
        asset = session.scalar(select(PartnerAsset))
        assert asset.kind == "ad" and asset.campaign_id == selected.id


@pytest.mark.integration
def test_active_campaign_cannot_enroll_ads_without_current_tier_entitlement(database):
    with database.begin() as session:
        account_id, product_id = seed_catalog(session)
        campaign = PartnerCampaign(account_id=account_id, **agreement(), targeting="all")
        session.add(campaign)
        session.flush()
        assert ensure_automatic_assets(session, account_id, NOW) == 1
        assets = session.scalars(
            select(PartnerAsset).where(PartnerAsset.account_id == account_id)
        ).all()
        assert len(assets) == 1
        assert assets[0].kind == "product" and assets[0].product_id == product_id
        assert assets[0].campaign_id is None


@pytest.mark.integration
def test_database_prevents_rewriting_automatic_attribution_and_cross_account_campaign(database):
    with database() as session:
        account_id, product_id = seed_catalog(session)
        ensure_automatic_assets(session, account_id, NOW)
        asset = session.scalar(select(PartnerAsset))
        session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                text("UPDATE partner_assets SET kind='ad' WHERE id=:id"), {"id": asset.id}
            )
        other = PartnerAccount(name="Other", tier="gold")
        session.add(other)
        session.flush()
        campaign = PartnerCampaign(account_id=other.id, **agreement(), targeting="all")
        session.add(campaign)
        session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                text("""INSERT INTO partner_assets
                    (id,account_id,product_id,name,kind,status,created_at,automatic,campaign_id)
                VALUES(:id,:account,:product,'Forged','ad','active',now(),true,:campaign)"""),
                {
                    "id": uuid.uuid4(),
                    "account": account_id,
                    "product": product_id,
                    "campaign": campaign.id,
                },
            )


@pytest.mark.integration
def test_admin_campaign_scope_inventory_and_automatic_attribution(admin_client, database):
    with database() as session:
        account_id, product_id = seed_catalog(session)
        account = session.get(PartnerAccount, account_id)
        account.tier, account.promotion_kind = "gold", "product"
        other_account, other_product = seed_catalog(session, provider="partner-two", name="Other")
        session.commit()
    prefix = f"/v1/admin/partner-accounts/{account_id}"
    payload = CampaignInput(
        **agreement(), targeting="selected", product_ids=[product_id]
    ).model_dump(mode="json")
    denied = admin_client.post(
        f"{prefix}/campaigns", json={**payload, "product_ids": [str(other_product)]}
    )
    assert denied.status_code == 422
    result = admin_client.post(f"{prefix}/campaigns", json=payload)
    assert result.status_code == 201, result.text
    campaign = result.json()
    campaign_id = campaign["id"]
    assert campaign["impressions"] == 0 and campaign["ctr"] is None
    listed = admin_client.get(f"{prefix}/campaigns?limit=1").json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == campaign_id
    assert admin_client.get(f"{prefix}/campaigns/{campaign_id}").status_code == 200
    assert (
        admin_client.get(
            f"/v1/admin/partner-accounts/{other_account}/campaigns/{campaign_id}"
        ).status_code
        == 404
    )
    selected = admin_client.get(
        f"{prefix}/inventory", params={"product_id": str(product_id)}
    ).json()
    assert selected["total"] == 1 and selected["items"][0]["id"] == str(product_id)
    assert not selected["items"][0]["globally_excluded"]
    assert admin_client.get(f"{prefix}/inventory", params={"q": "unmatched"}).json()["total"] == 0
    assert (
        admin_client.put(
            f"{prefix}/products/{other_product}/promotion", json={"excluded": True}
        ).status_code
        == 404
    )
    excluded = admin_client.put(
        f"{prefix}/products/{product_id}/promotion", json={"excluded": True}
    )
    assert excluded.status_code == 200 and excluded.json()["excluded"]
    assert admin_client.get(f"{prefix}/inventory").json()["items"][0]["excluded"]
    changed = admin_client.put(
        f"{prefix}/campaigns/{campaign_id}", json={**payload, "status": "paused"}
    )
    assert changed.status_code == 200 and changed.json()["status"] == "paused"
    with database() as session:
        assets = session.scalars(
            select(PartnerAsset).where(PartnerAsset.account_id == account_id)
        ).all()
        assert len(assets) == 2
        asset_id = next(asset.id for asset in assets if asset.campaign_id)
    mutation = admin_client.put(
        f"{prefix}/assets/{asset_id}",
        json={
            "name": "Rewrite",
            "kind": "product",
            "product_id": str(product_id),
            "status": "active",
        },
    )
    assert mutation.status_code == 409


@pytest.mark.integration
def test_admin_campaign_metrics_keep_campaign_and_organic_distinct(admin_client, database):
    from devfeed_core.models import PartnerDailyMetric, PartnerTrackedDailyMetric

    with database() as session:
        account_id, _ = seed_catalog(session)
        account = session.get(PartnerAccount, account_id)
        account.tier, account.promotion_kind = "gold", "product"
        campaign = PartnerCampaign(account_id=account_id, **agreement(), targeting="all")
        session.add(campaign)
        session.flush()
        ensure_automatic_assets(session, account_id, NOW)
        organic = session.scalar(select(PartnerAsset).where(PartnerAsset.campaign_id.is_(None)))
        sponsored = session.scalar(
            select(PartnerAsset).where(PartnerAsset.campaign_id == campaign.id)
        )
        session.add_all(
            [
                PartnerTrackedDailyMetric(
                    asset_id=organic.id, day=NOW.date(), impressions=999, clicks=99
                ),
                PartnerTrackedDailyMetric(
                    asset_id=sponsored.id, day=NOW.date(), impressions=20, clicks=2
                ),
                PartnerDailyMetric(asset_id=sponsored.id, day=NOW.date(), impressions=10, clicks=1),
            ]
        )
        session.commit()
        campaign_id = campaign.id
    response = admin_client.get(f"/v1/admin/partner-accounts/{account_id}/campaigns/{campaign_id}")
    assert response.status_code == 200
    assert response.json()["impressions"] == 30
    assert response.json()["clicks"] == 3
    assert response.json()["ctr"] == 10.0


@pytest.mark.integration
def test_one_canonical_product_has_independent_account_defaults(database):
    with database() as session:
        first_account, product_id = seed_catalog(session)
        second = PartnerAccount(name="Second partner", tier="diamond")
        session.add(second)
        session.flush()
        session.add(
            PartnerConnection(
                provider="shared-product-provider", account_id=second.id, enabled=True
            )
        )
        session.flush()
        product = session.get(PartnerProduct, product_id)
        session.add(
            PartnerListing(
                product_id=product_id,
                provider="shared-product-provider",
                external_id="shared",
                name=product.name,
                product_url=product.product_url,
                listing_url=product.product_url,
                tagline=product.tagline,
            )
        )
        session.add(
            PartnerProductPromotion(account_id=first_account, product_id=product_id, excluded=True)
        )
        session.flush()
        assert ensure_automatic_assets(session, now=NOW, product_ids=[product_id]) == 1
        assert session.scalar(select(PartnerAsset.account_id)) == second.id
        assert ensure_automatic_assets(session, now=NOW, product_ids=[]) == 0


@pytest.mark.integration
def test_admin_topic_filtered_campaign_requires_active_taxonomy(admin_client, database):
    from devfeed_core.models import Topic

    with database() as session:
        account_id, _ = seed_catalog(session)
        account = session.get(PartnerAccount, account_id)
        account.tier, account.promotion_kind = "gold", "product"
        topic = Topic(name="Kubernetes", slug="kubernetes", kind="technology", status="proposed")
        session.add(topic)
        session.commit()
        topic_id = topic.id
    prefix = f"/v1/admin/partner-accounts/{account_id}/campaigns"
    payload = CampaignInput(**agreement(), targeting="filters", topic_ids=[topic_id]).model_dump(
        mode="json"
    )
    assert admin_client.post(prefix, json=payload).status_code == 422
    with database() as session:
        session.get(Topic, topic_id).status = "active"
        session.commit()
    result = admin_client.post(prefix, json=payload)
    assert result.status_code == 201, result.text
    assert result.json()["topic_ids"] == [str(topic_id)]
