"""Rejected inventory cannot monopolize a bounded placement candidate batch."""

from datetime import timedelta

import pytest
from devfeed_core.models import (
    ArticleTopic,
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerListing,
    PartnerPlacementDelivery,
    PartnerProduct,
    PartnerProductPromotion,
)
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy import func, select
from test_partner_placement_rotation import KEY, issue, mark_served, seed_rotation

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("surface", ["feed", "topic", "article"])
@pytest.mark.parametrize("restriction", ["formats", "surfaces", "globally_excluded"])
def test_unserved_rejected_products_cannot_fill_the_candidate_pool(database, surface, restriction):
    catalog = seed_rotation(database, count=101)
    rejected, eligible = catalog["products"][:-1], catalog["products"][-1]
    with database.begin() as session:
        for product_id in rejected:
            if restriction == "globally_excluded":
                session.get(PartnerProduct, product_id).excluded = True
            else:
                session.add(
                    PartnerProductPromotion(
                        account_id=catalog["account"],
                        product_id=product_id,
                        excluded=False,
                        ad_formats=["engagement"] if restriction == "formats" else None,
                        promotion_surfaces=(
                            ["article" if surface != "article" else "feed"]
                            if restriction == "surfaces"
                            else None
                        ),
                    )
                )
    # NULL last-issued values for rejected products must not displace a valid
    # product just because that product has already received an appearance.
    mark_served(database, catalog, [eligible])
    for refresh in range(2):
        rows = issue(
            database,
            catalog,
            surface=surface,
            now=catalog["now"] + timedelta(seconds=refresh),
        )
        assert [row["product_id"] for row in rows] == [eligible]
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerPlacementDelivery)
                .join(PartnerAsset)
                .where(PartnerAsset.product_id.in_(rejected))
            )
            == 0
        )


def grounded_issue(database, catalog, surface, *, now=None):
    if surface != "feed":
        return issue(database, catalog, surface=surface, now=now)
    with database.begin() as session:
        if session.get(ArticleTopic, (catalog["article"], catalog["topic"])) is None:
            session.add(
                ArticleTopic(
                    article_id=catalog["article"],
                    topic_id=catalog["topic"],
                    role="primary",
                    relevance=1,
                    evidence="Python testing tutorial",
                )
            )
    query = PlacementQuery(surface="feed", article_ids=[catalog["article"]])
    with database.begin() as session:
        return deliver_placements(session, query, KEY, now=now or catalog["now"])


@pytest.mark.parametrize("surface", ["feed", "topic", "article"])
def test_campaign_priority_precedes_rotation_before_the_product_cutoff(database, surface):
    catalog = seed_rotation(database, count=101)
    prioritized = catalog["products"][-1]
    with database.begin() as session:
        campaign = PartnerCampaign(
            account_id=catalog["account"],
            name="Priority product beyond the first hundred",
            targeting="selected",
            product_ids=[str(prioritized)],
            status="active",
            priority=90,
            starts_at=catalog["now"] - timedelta(days=1),
            ends_at=catalog["now"] + timedelta(days=1),
        )
        session.add(campaign)
        session.flush()
        campaign_id = campaign.id
        ensure_automatic_assets(session, catalog["account"], catalog["now"])
    mark_served(database, catalog, [prioritized])
    for refresh in range(2):
        rows = grounded_issue(
            database,
            catalog,
            surface,
            now=catalog["now"] + timedelta(seconds=refresh),
        )
        assert [row["product_id"] for row in rows] == [prioritized]
        assert rows[0]["campaign_id"] == campaign_id


@pytest.mark.parametrize("surface", ["feed", "topic", "article"])
@pytest.mark.parametrize("rejection", ["budget", "audience", "destination"])
def test_live_rejections_advance_beyond_a_full_batch(database, surface, rejection):
    catalog = seed_rotation(database, count=102)
    rejected, eligible = catalog["products"][:-1], catalog["products"][-1]
    with database.begin() as session:
        if rejection == "destination":
            session.get(PartnerAccount, catalog["account"]).outbound_destination = "listing_url"
            for listing in session.scalars(
                select(PartnerListing).where(PartnerListing.product_id.in_(rejected))
            ):
                listing.listing_url = ""
            listing = session.scalar(
                select(PartnerListing).where(PartnerListing.product_id == eligible)
            )
            listing.listing_url = "https://catalog.example/eligible-product"
        else:
            if rejection == "audience":
                session.get(PartnerAccount, catalog["account"]).tier = "platinum"
            campaign = PartnerCampaign(
                account_id=catalog["account"],
                name="Rejected higher priority products",
                targeting="selected",
                product_ids=[str(product_id) for product_id in rejected],
                status="active",
                priority=90,
                daily_impression_limit=1 if rejection == "budget" else None,
                audience={"experience_levels": ["senior"]} if rejection == "audience" else None,
                starts_at=catalog["now"] - timedelta(days=1),
                ends_at=catalog["now"] + timedelta(days=1),
            )
            session.add(campaign)
            session.flush()
            ensure_automatic_assets(session, catalog["account"], catalog["now"])
    if rejection == "budget":
        # One product's live reservation consumes the shared campaign limit.
        # The remaining hundred are still never-issued, so they occupy the old
        # bounded NULL-first pool despite every one being undeliverable.
        mark_served(database, catalog, [rejected[0]])
    # Explicit agreements shadow the standing campaign, including when their
    # audience or budget rejects delivery. The standing alternative must not
    # bypass that agreement while the eligible product waits beyond the batch.
    rows = grounded_issue(database, catalog, surface)
    assert [row["product_id"] for row in rows] == [eligible]
    if rejection == "destination":
        assert rows[0]["url"].startswith("https://catalog.example/eligible-product?")
        assert "utm_source=devfeed" in rows[0]["url"]
