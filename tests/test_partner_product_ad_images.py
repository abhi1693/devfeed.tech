"""Account-specific ad images are managed, asynchronous and fenced across replacement."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import image_storage_tasks, image_tasks
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.image_jobs import retry_image
from devfeed_core.job_retries import retry_candidate
from devfeed_core.models import (
    ArticleImageJob,
    PartnerAccount,
    PartnerCampaign,
    PartnerListing,
    PartnerProduct,
    PartnerProductPromotion,
    utcnow,
)
from devfeed_core.object_storage import R2Storage
from devfeed_core.partner_catalog import merge_products, upsert_listing
from devfeed_core.partner_product_images import product_image_url, product_image_variants
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_core.partner_tools import ProductInput
from devfeed_http.partner_portal import (
    ProductPromotionInput,
    creative_preview,
    list_product_promotions,
    product_promotion,
    save_product_promotion,
)
from pydantic import ValidationError
from sqlalchemy import event, select
from test_partner_placements_integration import catalog as catalog
from test_partner_placements_integration import deliver, query
from test_partner_portal import admin_portal_client as admin_portal_client
from test_partner_portal import portal_client as portal_client
from test_partner_product_formats import owned_catalog
from test_partner_promotions import seed_catalog

CUSTOM = "https://creative.example/product-card.png"
OTHER = "https://creative.example/replacement.png"
PUBLIC = "https://images.example"


def managed(source, *, digest="custom", revision=1):
    return {
        "source_url": source,
        "version": "v1",
        "thumbnail_version": "v2",
        "subject_revision": revision,
        "hash": digest,
        "original_key": f"originals/{digest}",
        "source_width": 1200,
        "variants": [
            {"key": f"thumbnails/v2/{digest}/{width}.webp", "width": width}
            for width in (320, 640, 960)
        ],
    }


@pytest.fixture
def managed_storage(monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", PUBLIC)
    get_settings.cache_clear()
    monkeypatch.setattr(
        image_storage_tasks,
        "storage_client",
        lambda: R2Storage("test-images", client=SimpleNamespace(close=lambda: None)),
    )
    original = Mock(side_effect=lambda client, source: {**managed(source), "variants": []})
    monkeypatch.setattr(
        image_storage_tasks.ImageOriginalStore,
        "store",
        lambda self, *args: original(self.storage, *args),
    )
    variant = Mock(
        side_effect=lambda client, asset, width: {
            "key": f"thumbnails/v2/{asset['hash']}/{width}.webp",
            "width": width,
        }
    )
    monkeypatch.setattr(
        image_storage_tasks.ThumbnailVariantStore,
        "store",
        lambda self, *args: variant(self.storage, *args),
    )
    return original, variant


@pytest.mark.parametrize(
    "url",
    [
        "http://images.example/card.png",
        "https://127.0.0.1/card.png",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/card.png",
        "https://private.internal/card.png",
        "https://2130706433/card.png",
        "https://user:password@images.example/card.png",
        "https://images.example:8443/card.png",
        "https://[::1]/card.png",
        "data:image/png,abc",
        "file:///tmp/secret",
        "https://images.example/card\n.png",
        "",
    ],
)
def test_custom_ad_image_requires_a_public_https_source(url):
    with pytest.raises(ValidationError):
        ProductPromotionInput(ad_image_url=url)


def test_custom_image_selection_only_uses_complete_current_managed_assets(managed_storage):
    product = PartnerProduct(product_url="https://tool.example/", managed_image={})
    product.managed_image = {
        **managed("https://tool.example/og.png", digest="og"),
        "page_url": product.product_url,
    }
    policy = PartnerProductPromotion(ad_image_url=CUSTOM, ad_image_revision=1, managed_ad_image={})
    default_url = f"{PUBLIC}/thumbnails/v2/og/640.webp"
    assert product_image_url(product, policy) == default_url
    policy.managed_ad_image = managed(CUSTOM)
    assert product_image_url(product, policy) == f"{PUBLIC}/thumbnails/v2/custom/640.webp"
    assert product_image_url(product) == default_url
    assert [item["width"] for item in product_image_variants(product, policy)] == [320, 640, 960]
    # A changed canonical website does not discard an explicitly selected image.
    product.product_url = "https://replacement.example/"
    assert product_image_url(product, policy) == f"{PUBLIC}/thumbnails/v2/custom/640.webp"
    product.product_url = "https://tool.example/"
    for field, value in (
        ("source_url", OTHER),
        ("subject_revision", 0),
        ("version", "old"),
        ("variants", []),
    ):
        policy.managed_ad_image = {**managed(CUSTOM), field: value}
        assert product_image_url(product, policy) == default_url
    policy.managed_ad_image = managed(CUSTOM)
    policy.ad_image_url = None
    assert product_image_url(product, policy) == default_url


def save(database, account_id, product_id, source):
    with database.begin() as session:
        return save_product_promotion(
            session,
            session.get(PartnerAccount, account_id),
            product_id,
            ProductPromotionInput(ad_image_url=source),
        )


def latest_job(database, account_id, product_id):
    with database() as session:
        return session.scalar(
            select(ArticleImageJob)
            .where(
                ArticleImageJob.account_id == account_id,
                ArticleImageJob.product_id == product_id,
                ArticleImageJob.operation == "product-ad-image",
            )
            .order_by(ArticleImageJob.created_at.desc(), ArticleImageJob.id.desc())
            .limit(1)
        )


@pytest.mark.integration
def test_portal_image_override_is_account_scoped_and_clear_restores_og(
    database,
    managed_storage,
    portal_client,
    admin_portal_client,
):
    account, product_id = owned_catalog(database)
    portal, _ = portal_client
    admin, _ = admin_portal_client
    path = f"/v1/partner/accounts/{account}/products/{product_id}/promotion"
    admin_path = f"/v1/admin/partner-accounts/{account}/products/{product_id}/promotion"
    with database.begin() as session:
        product = session.get(PartnerProduct, product_id)
        product.managed_image = {**managed(CUSTOM, digest="og"), "page_url": product.product_url}
        before = product.revision, product.updated_at, product.verified_at
    default = f"{PUBLIC}/thumbnails/v2/og/640.webp"
    initial = portal.get(path).json()
    assert initial["ad_image_url"] is None and initial["ad_image_status"] == "default"
    assert initial["image_url"] == initial["default_image_url"] == default
    response = portal.put(path, json={"ad_image_url": CUSTOM})
    assert response.status_code == 200, response.text
    assert response.json()["ad_image_status"] == "queued"
    assert response.json()["image_url"] == default
    queued = latest_job(database, account, product_id)
    assert portal.put(path, json={"ad_image_url": CUSTOM}).status_code == 200
    assert latest_job(database, account, product_id).id == queued.id
    image_tasks.enrich_image(str(queued.id))
    ready = portal.get(path).json()
    assert ready["ad_image_status"] == "ready"
    assert ready["image_url"] == f"{PUBLIC}/thumbnails/v2/custom/640.webp"
    assert ready["default_image_url"] == default
    preview = portal.get(
        f"/v1/partner/accounts/{account}/creative-preview", params={"product_id": str(product_id)}
    ).json()["product"]
    assert preview["image_url"] == ready["image_url"] and preview["ad_image_status"] == "ready"
    rows = admin.get(f"/v1/admin/partner-accounts/{account}/products/promotions").json()
    assert rows[0]["image_url"] == ready["image_url"] and rows[0]["ad_image_status"] == "ready"
    inventory = portal.get(f"/v1/partner/accounts/{account}/inventory").json()
    assert inventory["items"][0]["ad_image_url"] == CUSTOM
    cleared = admin.put(admin_path, json={"ad_image_url": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["image_url"] == default and cleared.json()["ad_image_status"] == "default"
    with database() as session:
        product = session.get(PartnerProduct, product_id)
        assert (product.revision, product.updated_at, product.verified_at) == before
        policy = session.get(PartnerProductPromotion, (account, product_id))
        assert policy.managed_ad_image == {} and policy.ad_image_revision == 2
    with database.begin() as session:
        foreign_account, foreign_product = seed_catalog(session, provider="foreign-owner")
    foreign_path = f"/v1/partner/accounts/{foreign_account}/products/{product_id}/promotion"
    assert portal.put(foreign_path, json={"ad_image_url": CUSTOM}).status_code == 404
    assert (
        portal.put(
            path.replace(str(product_id), str(foreign_product)), json={"ad_image_url": CUSTOM}
        ).status_code
        == 404
    )


@pytest.mark.integration
def test_disabled_image_storage_rejects_new_override_but_allows_clear(database, portal_client):
    account, product_id = owned_catalog(database)
    portal, _ = portal_client
    path = f"/v1/partner/accounts/{account}/products/{product_id}/promotion"
    assert portal.put(path, json={"ad_image_url": CUSTOM}).status_code == 422
    assert portal.put(path, json={"ad_image_url": None}).status_code == 200
    with database() as session:
        assert (
            session.scalar(select(ArticleImageJob.id).where(ArticleImageJob.account_id == account))
            is None
        )


@pytest.mark.integration
@pytest.mark.parametrize("automatic", [True, False], ids=["automatic", "manual"])
def test_custom_image_reaches_delivery_without_leaking_to_another_account(
    database,
    catalog,
    managed_storage,
    automatic,
):
    product_id, account_id = catalog["free_product"], catalog["free"]
    with database.begin() as session:
        product = session.get(PartnerProduct, product_id)
        product.managed_image = {**managed(CUSTOM, digest="og"), "page_url": product.product_url}
        # The same canonical product may belong to multiple independent catalogs.
        session.add(
            PartnerListing(
                product_id=product_id,
                provider="paid",
                external_id="shared",
                name=product.name,
                product_url=product.product_url,
                listing_url=product.product_url,
                description=product.description,
            )
        )
    save(database, account_id, product_id, CUSTOM)
    image_tasks.enrich_image(str(latest_job(database, account_id, product_id).id))
    if not automatic:
        with database.begin() as session:
            account = session.get(PartnerAccount, account_id)
            account.tier, account.promotion_kind = "diamond", "product"
            session.add(
                PartnerCampaign(
                    account_id=account_id,
                    name="Selected product agreement",
                    status="active",
                    targeting="selected",
                    product_ids=[str(product_id)],
                    priority=100,
                    starts_at=utcnow() - timedelta(days=1),
                    ends_at=utcnow() + timedelta(days=1),
                )
            )
            session.flush()
            ensure_automatic_assets(session, account_id, product_ids=[product_id])
    custom = next(
        item for item in deliver(database, query(catalog)) if item["product_id"] == product_id
    )
    assert custom["image_url"] == f"{PUBLIC}/thumbnails/v2/custom/640.webp"
    assert [item["width"] for item in custom["image_variants"]] == [320, 640, 960]
    with database.begin() as session:
        session.get(PartnerAccount, account_id).status = "paused"
        ensure_automatic_assets(session, catalog["paid"], product_ids=[product_id])
        other = session.get(PartnerAccount, catalog["paid"])
        assert (
            product_promotion(session, other, product_id).image_url
            == f"{PUBLIC}/thumbnails/v2/og/640.webp"
        )
        assert (
            creative_preview(session, other, product_id).product.image_url
            == f"{PUBLIC}/thumbnails/v2/og/640.webp"
        )
    # Restrict the paid agreement to the shared product to make attribution deterministic.
    with database.begin() as session:
        session.get(PartnerCampaign, catalog["campaign"]).product_ids = [str(product_id)]
        session.get(PartnerCampaign, catalog["campaign"]).targeting = "selected"
    other = next(
        item for item in deliver(database, query(catalog)) if item["product_id"] == product_id
    )
    assert other["image_url"] == f"{PUBLIC}/thumbnails/v2/og/640.webp"


@pytest.mark.integration
@pytest.mark.parametrize(
    "replacement", [None, OTHER, CUSTOM], ids=["clear", "replace", "clear-and-reuse"]
)
def test_late_image_io_cannot_restore_a_replaced_or_cleared_override(
    database,
    managed_storage,
    replacement,
):
    account, product_id = owned_catalog(database)
    original, _ = managed_storage
    save(database, account, product_id, CUSTOM)
    old = latest_job(database, account, product_id)

    def changed(client, source):
        asset = original(client.storage, source)
        save(database, account, product_id, None if replacement == CUSTOM else replacement)
        if replacement == CUSTOM:
            save(database, account, product_id, CUSTOM)
        return asset

    image_storage_tasks.ImageOriginalStore.store = changed
    image_tasks.enrich_image(str(old.id))
    with database() as session:
        policy = session.get(PartnerProductPromotion, (account, product_id))
        assert policy.ad_image_url == replacement and policy.managed_ad_image == {}
        assert session.get(ArticleImageJob, old.id).outcome == "already_present"
        if replacement:
            new = latest_job(database, account, product_id)
            assert new.id != old.id and new.subject_revision > old.subject_revision


@pytest.mark.integration
def test_failed_custom_image_keeps_og_and_retry_resumes_variants(
    database, managed_storage, monkeypatch
):
    account, product_id = owned_catalog(database)
    original, _ = managed_storage
    with database.begin() as session:
        product = session.get(PartnerProduct, product_id)
        product.managed_image = {**managed(CUSTOM, digest="og"), "page_url": product.product_url}
    widths = []

    def variant(client, asset, width):
        widths.append(width)
        if width == 640 and widths.count(width) == 1:
            raise FeedError("Bad transform", reason="image_transform")
        return {"key": f"thumbnails/v2/custom/{width}.webp", "width": width}

    monkeypatch.setattr(image_storage_tasks.ThumbnailVariantStore, "store", variant)
    save(database, account, product_id, CUSTOM)
    job = latest_job(database, account, product_id)
    image_tasks.enrich_image(str(job.id))
    with database.begin() as session:
        failed = session.get(ArticleImageJob, job.id)
        assert failed.status == "failed"
        policy = product_promotion(session, session.get(PartnerAccount, account), product_id)
        assert (
            policy.ad_image_status == "failed"
            and policy.image_url == f"{PUBLIC}/thumbnails/v2/og/640.webp"
        )
        retry = retry_image(session, job.id)
        retry_id = retry.id
        assert [item["width"] for item in retry.storage["variants"]] == [320]
    image_tasks.enrich_image(str(retry_id))
    assert original.call_count == 1 and widths == [320, 640, 640, 960]
    with database() as session:
        assert (
            product_promotion(
                session, session.get(PartnerAccount, account), product_id
            ).ad_image_status
            == "ready"
        )


@pytest.mark.integration
def test_unchanged_import_and_alias_merge_preserve_account_specific_choices(
    database, managed_storage
):
    account, product_id = owned_catalog(database)
    save(database, account, product_id, CUSTOM)
    image_tasks.enrich_image(str(latest_job(database, account, product_id).id))
    with database.begin() as session:
        listing = session.scalar(
            select(PartnerListing).where(PartnerListing.product_id == product_id)
        )
        item = ProductInput.model_validate(
            {
                field: getattr(listing, field)
                for field in ProductInput.model_fields
                if hasattr(listing, field)
            }
        )
        product = upsert_listing(session, item, uuid.uuid4())
        policy = session.get(PartnerProductPromotion, (account, product_id))
        assert (
            policy.ad_image_url == CUSTOM
            and product_image_url(product, policy) == f"{PUBLIC}/thumbnails/v2/custom/640.webp"
        )
        recipient = PartnerProduct(
            name="Canonical",
            product_url="https://canonical.example/",
            description="Canonical product",
        )
        session.add(recipient)
        session.flush()
        recipient_id = recipient.id
        merge_products(session, product, recipient)
        carried = session.get(PartnerProductPromotion, (account, recipient_id))
        assert carried.ad_image_url == CUSTOM
        assert product_image_url(recipient, carried) == f"{PUBLIC}/thumbnails/v2/custom/640.webp"
    # A prior explicit reset on the canonical product must win over an alias image.
    with database.begin() as session:
        recipient = PartnerProduct(
            name="Explicit canonical",
            product_url="https://explicit.example/",
            description="Another product",
        )
        session.add(recipient)
        session.flush()
        session.add(
            PartnerProductPromotion(
                account_id=account, product_id=recipient.id, ad_image_revision=2
            )
        )
        session.flush()
        merge_products(session, session.get(PartnerProduct, recipient_id), recipient)
        assert session.get(PartnerProductPromotion, (account, recipient.id)).ad_image_url is None


@pytest.mark.integration
def test_retry_candidates_and_admin_list_keep_accounts_independent(
    database, managed_storage, admin_client
):
    account, product_id = owned_catalog(database)
    with database.begin() as session:
        other = PartnerAccount(name="Other owner", tier="bronze")
        session.add(other)
        session.flush()
        failed = ArticleImageJob(
            account_id=account,
            product_id=product_id,
            subject_revision=1,
            operation="product-ad-image",
            status="failed",
        )
        running = ArticleImageJob(
            account_id=other.id,
            product_id=product_id,
            subject_revision=1,
            operation="product-ad-image",
            status="running",
        )
        session.add_all([failed, running])
        session.flush()
        failed_id = failed.id
    with database() as session:
        assert (
            session.scalar(
                select(ArticleImageJob.id).where(
                    ArticleImageJob.id == failed_id, retry_candidate(ArticleImageJob)
                )
            )
            == failed_id
        )
    response = admin_client.get("/v1/admin/jobs/images", params={"sort": "status"})
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["items"] if row["id"] == str(failed_id))
    assert row["retryable"] and row["details"]["account_id"] == str(account)


@pytest.mark.integration
def test_admin_promotion_image_metadata_uses_a_constant_query_count(database, managed_storage):
    account, product_id = owned_catalog(database)
    save(database, account, product_id, CUSTOM)
    statements = []
    engine = database.kw["bind"]

    def count(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        with database() as session:
            rows = list_product_promotions(session, account)
            assert rows[0].ad_image_status == "queued"
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(statements) == 2
