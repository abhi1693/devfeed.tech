"""Catalog observations preserve contextual preparation until canonical facts change."""

import uuid
from datetime import timedelta

import pytest
from devfeed_core import partner_catalog
from devfeed_core.models import (
    PartnerArticleMatch,
    PartnerConnection,
    PartnerListing,
    PartnerPipelineJob,
    PartnerProduct,
    PartnerProductTopic,
    PartnerProductURL,
)
from devfeed_core.partner_tools import ProductInput
from devfeed_core.urls import canonicalize_url, fingerprint
from sqlalchemy import select

from tests.partners.placements.test_placement_rotation import issue, seed_rotation

pytestmark = pytest.mark.integration

PRODUCT_FIELDS = (
    "name",
    "tagline",
    "pricing",
    "status",
    "revision",
    "updated_at",
    "verified_at",
    "assessment_revision",
    "assessment",
    "technologies",
    "evidence",
    "metadata_listing_id",
)


@pytest.fixture
def prepared_catalog(database):
    catalog = seed_rotation(database, count=1)
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["products"][0])
        listing = session.scalar(
            select(PartnerListing).where(PartnerListing.product_id == product.id)
        )
        product.metadata_listing_id = listing.id
        product.assessment_revision = product.revision
        product.assessment = {"state": "qualified", "reason": "Verified Python tooling."}
        listing.seen_generation = uuid.uuid4()
        url = canonicalize_url(product.product_url)
        session.add(
            PartnerProductURL(
                url_hash=fingerprint(url),
                url=url,
                product_id=product.id,
                verified_at=catalog["now"],
            )
        )
        item = ProductInput.model_validate(
            {
                field: getattr(listing, field)
                for field in ProductInput.model_fields
                if hasattr(listing, field)
            }
        )
        catalog.update(
            item=item,
            listing=listing.id,
            generation=listing.seen_generation,
            observed_at=listing.updated_at,
            product_before={field: getattr(product, field) for field in PRODUCT_FIELDS},
        )
    return catalog


def reimport(database, catalog, monkeypatch, *, item=None, minutes=5):
    observed_at = catalog["now"] + timedelta(minutes=minutes)
    monkeypatch.setattr(partner_catalog, "utcnow", lambda: observed_at)
    generation = uuid.uuid4()
    with database.begin() as session:
        product = partner_catalog.upsert_listing(session, item or catalog["item"], generation)
        assert product.id == catalog["products"][0]
    return generation, observed_at


def assert_product_unchanged(database, catalog):
    with database() as session:
        product = session.get(PartnerProduct, catalog["products"][0])
        assert {field: getattr(product, field) for field in PRODUCT_FIELDS} == catalog[
            "product_before"
        ]


@pytest.mark.parametrize("surface", ["article", "topic"])
def test_unchanged_import_keeps_prepared_contextual_placements(
    database, prepared_catalog, monkeypatch, surface
):
    catalog = prepared_catalog
    assert issue(database, catalog, surface=surface)[0]["product_id"] == catalog["products"][0]
    generation, observed_at = reimport(database, catalog, monkeypatch)

    with database() as session:
        listing = session.get(PartnerListing, catalog["listing"])
        assert listing.seen_generation == generation != catalog["generation"]
        assert listing.updated_at == observed_at > catalog["observed_at"]
        assert listing.active and listing.identity_status == "resolved"
    assert_product_unchanged(database, catalog)
    rows = issue(database, catalog, surface=surface, now=observed_at)
    assert [row["product_id"] for row in rows] == catalog["products"]


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "Renamed Python tool"},
        {"tagline": "Verifies signatures on Python packages before installation."},
        {"pricing": "paid"},
    ],
)
def test_changed_primary_metadata_invalidates_old_context_even_after_requalification(
    database, prepared_catalog, monkeypatch, changes
):
    catalog = prepared_catalog
    for surface in ("article", "topic"):
        assert issue(database, catalog, surface=surface)
    item = catalog["item"].model_copy(update=changes)
    _, observed_at = reimport(database, catalog, monkeypatch, item=item)

    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["products"][0])
        assert product.revision == catalog["product_before"]["revision"] + 1
        assert product.updated_at == observed_at > catalog["product_before"]["updated_at"]
        assert all(getattr(product, field) == value for field, value in changes.items())
        assert product.status == "pending" and product.verified_at is None
        assert product.technologies == [] and product.evidence == []
        assert product.assessment_revision == 0
        for model, context_id in (
            (PartnerArticleMatch, catalog["article"]),
            (PartnerProductTopic, catalog["topic"]),
        ):
            prepared = session.get(model, (product.id, context_id))
            assert prepared.product_revision == catalog["product_before"]["revision"]
            assert prepared.product_updated_at == catalog["product_before"]["updated_at"]
        # Approval alone cannot make a contextual decision based on old facts current.
        product.status, product.verified_at = "approved", observed_at
        product.assessment_revision = product.revision
        product.technologies = catalog["product_before"]["technologies"]
        product.evidence = catalog["product_before"]["evidence"]
    for surface in ("article", "topic"):
        assert issue(database, catalog, surface=surface, now=observed_at) == []


def test_listing_provenance_change_does_not_invalidate_canonical_context(
    database, prepared_catalog, monkeypatch
):
    catalog = prepared_catalog
    item = catalog["item"].model_copy(
        update={
            "attribution": "Observed on the launch platform's revised catalog",
            "listing_url": "https://catalog.example/updated/python-tool",
        }
    )
    generation, observed_at = reimport(database, catalog, monkeypatch, item=item)
    with database() as session:
        listing = session.get(PartnerListing, catalog["listing"])
        assert listing.attribution == item.attribution
        assert listing.listing_url == item.listing_url
        assert listing.updated_at == observed_at
        assert listing.seen_generation == generation
    assert_product_unchanged(database, catalog)
    for surface in ("article", "topic"):
        assert issue(database, catalog, surface=surface, now=observed_at)


def test_secondary_platform_facts_do_not_replace_the_prepared_canonical_source(
    database, prepared_catalog, monkeypatch
):
    catalog = prepared_catalog
    with database.begin() as session:
        session.add(PartnerConnection(provider="secondary-rotation", account_id=catalog["account"]))
    item = catalog["item"].model_copy(
        update={
            "provider": "secondary-rotation",
            "external_id": "secondary-tool",
            "name": "Secondary platform's title",
            "tagline": "The secondary platform describes this tool differently.",
            "pricing": "paid",
            "listing_url": "https://secondary.example/products/tool",
        }
    )
    generation, observed_at = reimport(database, catalog, monkeypatch, item=item)
    with database() as session:
        secondary = session.scalar(
            select(PartnerListing).where(PartnerListing.provider == item.provider)
        )
        assert secondary.product_id == catalog["products"][0]
        assert secondary.name == item.name and secondary.tagline == item.tagline
        assert secondary.pricing == item.pricing
        assert secondary.updated_at == observed_at
        assert secondary.seen_generation == generation
        assert secondary.identity_status == "resolved"
    assert_product_unchanged(database, catalog)
    for surface in ("article", "topic"):
        assert issue(database, catalog, surface=surface, now=observed_at)


def test_identity_loss_and_revival_advance_semantic_freshness(
    database, prepared_catalog, monkeypatch
):
    catalog = prepared_catalog
    unresolved = catalog["item"].model_copy(
        update={"product_url": "https://unverified-destination.example/tool"}
    )
    _, withdrawn_at = reimport(database, catalog, monkeypatch, item=unresolved)
    with database() as session:
        product = session.get(PartnerProduct, catalog["products"][0])
        listing = session.get(PartnerListing, catalog["listing"])
        assert product.revision == catalog["product_before"]["revision"] + 1
        assert product.status == "withdrawn" and product.verified_at is None
        assert product.updated_at == withdrawn_at
        assert listing.identity_status == "unresolved"

    generation, restored_at = reimport(database, catalog, monkeypatch, minutes=10)
    with database() as session:
        product = session.get(PartnerProduct, catalog["products"][0])
        listing = session.get(PartnerListing, catalog["listing"])
        assert product.revision == catalog["product_before"]["revision"] + 2
        assert product.status == "pending" and product.verified_at is None
        assert product.updated_at == restored_at > withdrawn_at
        assert product.assessment_revision == 0
        assert listing.active and listing.identity_status == "resolved"
        assert listing.seen_generation == generation and listing.updated_at == restored_at


def test_unchanged_import_still_requests_assessment_when_approval_has_expired(
    database, prepared_catalog, monkeypatch
):
    catalog = prepared_catalog
    with database.begin() as session:
        session.get(PartnerProduct, catalog["products"][0]).verified_at = catalog[
            "now"
        ] - timedelta(days=91)
    reimport(database, catalog, monkeypatch)
    with database() as session:
        product = session.get(PartnerProduct, catalog["products"][0])
        assert product.revision == catalog["product_before"]["revision"]
        assert product.updated_at == catalog["product_before"]["updated_at"]
        assert product.status == "pending" and product.verified_at is None
        jobs = session.scalars(
            select(PartnerPipelineJob).where(PartnerPipelineJob.product_id == product.id)
        ).all()
        assert len(jobs) == 1
        assert jobs[0].operation == "assess" and jobs[0].status == "queued"
        assert jobs[0].payload["revision"] == product.revision
