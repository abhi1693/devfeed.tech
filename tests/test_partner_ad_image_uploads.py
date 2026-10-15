"""Product uploads preserve aspect ratio, scope storage, and fence asynchronous URL work."""

import io
from unittest.mock import Mock

import pytest
from devfeed_aggregator import image_storage_tasks, image_tasks
from devfeed_core.models import (
    ArticleImageJob,
    PartnerAccount,
    PartnerProduct,
    PartnerProductPromotion,
)
from devfeed_core.object_storage import R2Storage
from devfeed_core.partner_catalog import merge_products
from devfeed_core.partner_product_images import (
    AD_IMAGE_SIZES,
    ad_image_prefix,
    product_image_variants,
)
from devfeed_http import image_uploads
from devfeed_http.image_uploads import (
    CACHE_CONTROL,
    MAX_BYTES,
)
from devfeed_http.partner_ad_images import ProductAdImageStorage
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_avatar_uploads import FakeR2, picture
from test_partner_portal import admin_portal_client as admin_portal_client
from test_partner_portal import portal_client as portal_client
from test_partner_product_ad_images import CUSTOM, latest_job, save
from test_partner_product_ad_images import managed_storage as managed_storage
from test_partner_product_formats import owned_catalog
from test_partner_promotions import seed_catalog


@pytest.fixture
def upload_catalog(database, managed_storage, monkeypatch, portal_client, admin_portal_client):
    account, product = owned_catalog(database)
    r2 = FakeR2()
    monkeypatch.setattr(
        image_uploads, "image_upload_storage", lambda: R2Storage("ad-test-images", client=r2)
    )
    monkeypatch.setattr(
        image_storage_tasks, "storage_client", lambda: R2Storage("ad-test-images", client=r2)
    )
    portal, identity = portal_client
    admin, admin_identity = admin_portal_client
    return account, product, portal, admin, r2, identity, admin_identity


def path(account, product, admin=False):
    prefix = "admin/partner-accounts" if admin else "partner/accounts"
    return f"/v1/{prefix}/{account}/products/{product}/ad-image"


def upload(client, account, product, *, admin=False, body=None, mime="image/png"):
    return client.post(
        path(account, product, admin),
        files={"file": ("../private.png", body or picture(size=(1200, 800)), mime)},
    )


@pytest.mark.integration
@pytest.mark.parametrize("admin", [False, True])
def test_product_upload_uses_fixed_versioned_variants_and_keeps_other_policy(
    database, upload_catalog, admin
):
    account, product, portal, admin_client, r2, *_ = upload_catalog
    client = admin_client if admin else portal
    portal_policy = f"/v1/partner/accounts/{account}/products/{product}/promotion"
    assert (
        portal.put(
            portal_policy, json={"promotion_surfaces": ["feed"], "excluded": True}
        ).status_code
        == 200
    )
    with database() as session:
        item = session.get(PartnerProduct, product)
        facts = item.revision, item.updated_at, item.verified_at
    first = upload(client, account, product, admin=admin)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["image_url"] == body["ad_image_url"] and body["ad_image_status"] == "ready"
    assert body["excluded"] and body["promotion_surfaces"] == ["feed"]
    assert str(account) not in body["image_url"] and str(product) not in body["image_url"]
    prefix = ad_image_prefix(account, product)
    assert set(r2.objects) == {f"{prefix}/{size}.webp" for size in AD_IMAGE_SIZES}
    for key, encoded in r2.objects.items():
        slot = int(key.rsplit("/", 1)[1].split(".")[0])
        with Image.open(io.BytesIO(encoded)) as result:
            assert result.size == (slot, round(slot * 2 / 3))
            assert result.format == "WEBP" and not result.getexif()
    assert all(item["CacheControl"] == CACHE_CONTROL for item in r2.puts)
    second = upload(client, account, product, admin=admin, body=picture("blue", size=(1200, 800)))
    assert second.status_code == 200 and second.json()["image_url"] != body["image_url"]
    assert len(r2.objects) == 3
    with database() as session:
        item = session.get(PartnerProduct, product)
        policy = session.get(PartnerProductPromotion, (account, product))
        variants = product_image_variants(item, policy)
        assert len(variants) == 3 and all("?v=" in item["url"] for item in variants)
        assert (item.revision, item.updated_at, item.verified_at) == facts
        assert (
            session.scalar(select(ArticleImageJob.id).where(ArticleImageJob.account_id == account))
            is None
        )
    unchanged = portal.put(portal_policy, json={"ad_image_url": second.json()["ad_image_url"]})
    assert unchanged.status_code == 200 and unchanged.json()["ad_image_status"] == "ready"
    assert unchanged.json()["image_url"] == second.json()["image_url"]
    cleared = portal.put(portal_policy, json={"ad_image_url": None})
    assert cleared.status_code == 200 and cleared.json()["ad_image_status"] == "default"
    assert not r2.objects


@pytest.mark.integration
@pytest.mark.parametrize("admin", [False, True])
@pytest.mark.parametrize("change", ["revision", "variant"])
def test_editing_owned_upload_url_preserves_bytes_and_policy(
    database, upload_catalog, monkeypatch, admin, change
):
    account, product, portal, admin_client, r2, *_ = upload_catalog
    client = admin_client if admin else portal
    first = upload(client, account, product, admin=admin).json()
    source = first["ad_image_url"]
    changed = (
        source.split("?", 1)[0] + "?v=edited"
        if change == "revision"
        else source.replace("/800.webp", "/400.webp")
    )
    assert changed != source
    old = dict(r2.objects)
    policy_url = path(account, product, admin).removesuffix("/ad-image") + "/promotion"
    with database() as session:
        policy = session.get(PartnerProductPromotion, (account, product))
        before = policy.ad_image_url, policy.ad_image_revision, dict(policy.managed_ad_image)
    storage = Mock(side_effect=AssertionError("edited upload URL reached storage"))
    with monkeypatch.context() as patch:
        patch.setattr("devfeed_http.partner_ad_images.ProductAdImageStorage", storage)
        rejected = client.put(policy_url, json={"ad_image_url": changed})
        assert rejected.status_code == 422, rejected.text
        assert "Upload a new file or use another image URL" in rejected.json()["detail"]
        unchanged = client.put(policy_url, json={"ad_image_url": source})
        assert unchanged.status_code == 200 and unchanged.json()["image_url"] == source
    storage.assert_not_called()
    assert r2.objects == old
    with database() as session:
        policy = session.get(PartnerProductPromotion, (account, product))
        assert (policy.ad_image_url, policy.ad_image_revision, policy.managed_ad_image) == before
    # Normal external-source replacement and explicit clearing remain supported.
    external = client.put(policy_url, json={"ad_image_url": CUSTOM})
    assert external.status_code == 200 and external.json()["ad_image_status"] == "queued"
    assert not r2.objects
    cleared = client.put(policy_url, json={"ad_image_url": None})
    assert cleared.status_code == 200 and cleared.json()["ad_image_status"] == "default"


@pytest.mark.integration
@pytest.mark.parametrize("size", [(120, 80), (10, 1000)])
def test_small_and_portrait_uploads_have_unique_srcset_widths(database, upload_catalog, size):
    account, product, client, _, r2, *_ = upload_catalog
    response = upload(client, account, product, body=picture(size=size))
    assert response.status_code == 200, response.text
    with database() as session:
        item = session.get(PartnerProduct, product)
        policy = session.get(PartnerProductPromotion, (account, product))
        widths = [item["width"] for item in product_image_variants(item, policy)]
        assert len(widths) == len(set(widths)) == 1
    assert len(r2.objects) == 3


@pytest.mark.integration
def test_failed_upload_and_database_commit_restore_previous_bytes_and_metadata(
    database, upload_catalog, monkeypatch
):
    account, product, client, _, r2, *_ = upload_catalog
    first = upload(client, account, product).json()
    old = dict(r2.objects)
    r2.fail_next = f"{ad_image_prefix(account, product)}/800.webp"
    response = upload(client, account, product, body=picture("blue"))
    assert response.status_code == 503 and r2.objects == old
    with monkeypatch.context() as patch:
        patch.setattr(
            Session, "commit", lambda self: (_ for _ in ()).throw(RuntimeError("commit failed"))
        )
        with pytest.raises(RuntimeError, match="commit failed"):
            upload(client, account, product, body=picture("green"))
        assert r2.objects == old
    with database() as session:
        policy = session.get(PartnerProductPromotion, (account, product))
        assert policy.ad_image_url == first["ad_image_url"]
    # Cleanup participates in rollback as well.
    with monkeypatch.context() as patch:
        patch.setattr(
            Session, "commit", lambda self: (_ for _ in ()).throw(RuntimeError("commit failed"))
        )
        with pytest.raises(RuntimeError):
            client.put(
                f"/v1/partner/accounts/{account}/products/{product}/promotion",
                json={"ad_image_url": None},
            )
        assert r2.objects == old


@pytest.mark.integration
def test_invalid_foreign_and_revoked_uploads_never_decode_or_write(
    database, upload_catalog, monkeypatch
):
    account, product, client, admin, r2, identity, admin_identity = upload_catalog
    for body, mime, status in (
        (b"<svg/>", "image/svg+xml", 415),
        (b"corrupt", "image/png", 422),
        (b"x" * (MAX_BYTES + 1), "image/png", 413),
    ):
        assert upload(client, account, product, body=body, mime=mime).status_code == status
    with database.begin() as session:
        _, foreign = seed_catalog(session, provider="foreign-upload")
    normalize = Mock(side_effect=AssertionError("foreign product was decoded"))
    monkeypatch.setattr(ProductAdImageStorage, "normalize", normalize)
    assert upload(client, account, foreign).status_code == 404
    identity.subject = "revoked"
    assert upload(client, account, product).status_code == 404
    admin_identity.roles = ["admin"]
    assert upload(admin, account, product, admin=True).status_code == 403
    normalize.assert_not_called()
    assert not r2.objects and not r2.puts


@pytest.mark.integration
def test_upload_rate_limit_stops_decode_before_storage(upload_catalog, monkeypatch):
    account, product, client, _, r2, *_ = upload_catalog
    normalize = Mock(side_effect=AssertionError("rate limited upload was decoded"))
    monkeypatch.setattr(ProductAdImageStorage, "normalize", normalize)
    for _ in range(10):
        assert (
            upload(client, account, product, body=b"<svg/>", mime="image/svg+xml").status_code
            == 415
        )
    limited = upload(client, account, product, body=b"<svg/>", mime="image/svg+xml")
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
    assert not r2.objects and not r2.puts
    normalize.assert_not_called()


@pytest.mark.integration
def test_upload_invalidates_inflight_url_job_and_url_replace_removes_fixed_objects(
    database, upload_catalog, managed_storage, monkeypatch
):
    account, product, client, _, r2, *_ = upload_catalog
    original, _ = managed_storage
    save(database, account, product, CUSTOM)
    old = latest_job(database, account, product)

    def replaced(store, source):
        asset = original(store.storage, source)
        assert upload(client, account, product).status_code == 200
        return asset

    monkeypatch.setattr(image_storage_tasks.ImageOriginalStore, "store", replaced)
    image_tasks.enrich_image(str(old.id))
    with database() as session:
        policy = session.get(PartnerProductPromotion, (account, product))
        assert policy.managed_ad_image["upload"]
        assert session.get(ArticleImageJob, old.id).outcome == "already_present"
    replaced = client.put(
        f"/v1/partner/accounts/{account}/products/{product}/promotion",
        json={"ad_image_url": CUSTOM},
    )
    assert replaced.status_code == 200 and replaced.json()["ad_image_status"] == "queued"
    assert not r2.objects


@pytest.mark.integration
def test_merge_transfers_upload_cleanup_and_discards_multi_hop_alias_objects(
    database, upload_catalog
):
    account, product_id, client, _, r2, *_ = upload_catalog
    assert upload(client, account, product_id).status_code == 200
    original_prefix = ad_image_prefix(account, product_id)
    with database.begin() as session:
        alias = PartnerProduct(
            name="Alias recipient",
            product_url="https://alias.example/",
            tagline="Alias product",
        )
        canonical = PartnerProduct(
            name="Final canonical",
            product_url="https://canonical.example/",
            tagline="Final product",
        )
        session.add_all([alias, canonical])
        session.flush()
        alias_id, canonical_id = alias.id, canonical.id
        merge_products(session, session.get(PartnerProduct, product_id), alias)
        inherited = session.get(PartnerProductPromotion, (account, alias_id))
        assert inherited.managed_ad_image["object_prefix"] == original_prefix
        # Final canonical has an explicit choice, so discard inherited upload.
        session.add(
            PartnerProductPromotion(
                account_id=account, product_id=canonical_id, ad_image_revision=1
            )
        )
        session.flush()
        merge_products(session, alias, canonical)
        cleanup = session.scalar(
            select(ArticleImageJob).where(
                ArticleImageJob.product_id == alias_id, ArticleImageJob.status == "queued"
            )
        )
        assert cleanup.storage["cleanup_upload"] == original_prefix
        cleanup_id = cleanup.id
    image_tasks.enrich_image(str(cleanup_id))
    assert not r2.objects
    with database() as session:
        assert session.get(ArticleImageJob, cleanup_id).outcome == "found"


@pytest.mark.integration
def test_account_update_contention_returns_retry_without_holding_product_lock(
    database, upload_catalog
):
    account, product, client, _, r2, *_ = upload_catalog
    with database.begin() as editor:
        editor.scalar(select(PartnerAccount).where(PartnerAccount.id == account).with_for_update())
        response = upload(client, account, product)
        assert response.status_code == 503 and response.headers["Retry-After"] == "1"
        # Rejection has rolled back its product lock, preventing an enrollment deadlock.
        editor.scalar(
            select(PartnerProduct).where(PartnerProduct.id == product).with_for_update(nowait=True)
        )
    assert not r2.puts
