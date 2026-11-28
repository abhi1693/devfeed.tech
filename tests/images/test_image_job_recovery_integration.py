"""Durable image jobs reject stale inputs and preserve valid retry checkpoints."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from devfeed_core import image_jobs
from devfeed_core.config import get_settings
from devfeed_core.logos import request_logo
from devfeed_core.managed_images import ARTICLE_THUMBNAIL_LAYOUT, THUMBNAIL_VERSION
from devfeed_core.models import (
    Article,
    ArticleImageJob,
    PartnerAccount,
    PartnerProduct,
    PartnerProductPromotion,
)
from devfeed_core.partner_product_images import (
    ad_image_prefix,
    request_ad_image_cleanup,
    request_product_ad_image,
    request_product_image,
)
from devfeed_core.services import OperationConflict
from devfeed_core.urls import fingerprint
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.fixture
def storage_enabled(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "image_storage_enabled", True)
    monkeypatch.setattr(get_settings(), "image_public_url", "https://images.example")
    return database


def add_article(session, *, image=True, managed=None):
    url = f"https://publisher.example/{uuid.uuid4()}"
    article = Article(
        canonical_url=url,
        url_hash=fingerprint(url),
        title="Preview image recovery",
        image_url=f"{url}.png" if image else None,
        managed_image=managed or {},
    )
    session.add(article)
    session.flush()
    return article


def add_product(session):
    product = PartnerProduct(
        name="Preview product", product_url="https://product.example", tagline="Preview recovery"
    )
    session.add(product)
    session.flush()
    return product


def managed_asset(source, *, version=THUMBNAIL_VERSION, digest="original"):
    return {
        "source_url": source,
        "version": "v1",
        "thumbnail_version": version,
        "hash": digest,
        "original_key": f"originals/{digest}",
        "source_width": 960,
        "variants": [{"key": f"thumbnails/{version}/{digest}/960.webp", "width": 960}],
    }


def assert_unclaimed(job):
    assert job.attempts == 0
    assert job.lease_token is None
    assert job.lease_until is None


def test_published_article_image_requests_require_private_maintenance(
    storage_enabled, publish_for_read_test
):
    with storage_enabled.begin() as session:
        article = add_article(session, image=False)
        article_id = article.id

    publish_for_read_test([article_id])

    with storage_enabled.begin() as session:
        article = session.get(Article, article_id)
        assert article.publication_status == "published"
        assert article.published_to_feed_at is not None
        assert list(session.scalars(select(ArticleImageJob.id))) == []
        assert image_jobs.request_image(session, article_id, automatic=True) is None
        with pytest.raises(OperationConflict, match="article maintenance"):
            image_jobs.request_image(session, article_id, refresh=True)
        assert article.image_url is None
        assert article.publication_status == "published"
        assert list(session.scalars(select(ArticleImageJob.id))) == []


@pytest.mark.parametrize("kind", ["article", "product-image", "product-logo"])
def test_storage_disabled_after_dispatch_keeps_image_job_queued(storage_enabled, monkeypatch, kind):
    with storage_enabled.begin() as session:
        if kind == "article":
            job = image_jobs.request_image(session, add_article(session).id)
        else:
            product = add_product(session)
            job = (
                request_product_image(session, product.id)
                if kind == "product-image"
                else request_logo(session, PartnerProduct, product.id)
            )
        job_id = job.id
    monkeypatch.setattr(get_settings(), "image_storage_enabled", False)

    with storage_enabled.begin() as session:
        assert image_jobs.claim_image(session, job_id) is None
        job = session.get(ArticleImageJob, job_id)
        assert job.status == "queued"
        assert_unclaimed(job)


@pytest.mark.parametrize("change", ["url", "excluded", "merged"])
def test_product_preview_claim_retires_stale_inputs_and_only_requeues_eligible_product(
    storage_enabled, change
):
    with storage_enabled.begin() as session:
        product = add_product(session)
        product_id = product.id
        job_id = request_product_image(session, product.id).id
        if change == "url":
            product.product_url = "https://replacement.example"
        elif change == "excluded":
            product.excluded = True
        else:
            product.merged_into_id = add_product(session).id

    with storage_enabled.begin() as session:
        assert image_jobs.claim_image(session, job_id) is None
        old = session.get(ArticleImageJob, job_id)
        assert (old.status, old.outcome) == ("succeeded", "already_present")
        assert_unclaimed(old)
        pending = list(
            session.scalars(
                select(ArticleImageJob).where(
                    ArticleImageJob.product_id == product_id,
                    ArticleImageJob.operation == "product-image",
                    ArticleImageJob.status == "queued",
                )
            )
        )
        if change == "url":
            assert len(pending) == 1
            assert pending[0].image_url == "https://replacement.example"
            assert pending[0].id != job_id
        else:
            assert pending == []


@pytest.mark.parametrize("change", ["url", "revision", "policy-removed"])
def test_ad_image_claim_rejects_replaced_or_removed_account_policy(storage_enabled, change):
    with storage_enabled.begin() as session:
        product = add_product(session)
        account = PartnerAccount(name="Preview advertiser", tier="silver")
        session.add(account)
        session.flush()
        policy = PartnerProductPromotion(
            account_id=account.id,
            product_id=product.id,
            ad_image_url="https://creative.example/old.png",
            ad_image_revision=1,
        )
        session.add(policy)
        session.flush()
        job_id = request_product_ad_image(session, account.id, product.id).id
        if change == "url":
            policy.ad_image_url = "https://creative.example/new.png"
        elif change == "revision":
            policy.ad_image_revision += 1
        else:
            session.delete(policy)

    with storage_enabled.begin() as session:
        assert image_jobs.claim_image(session, job_id) is None
        job = session.get(ArticleImageJob, job_id)
        assert (job.status, job.outcome) == ("succeeded", "already_present")
        assert_unclaimed(job)


def test_publisher_image_arriving_before_discovery_claim_queues_storage_instead(storage_enabled):
    with storage_enabled.begin() as session:
        article = add_article(session, image=False)
        article_id = article.id
        job_id = image_jobs.request_image(session, article.id).id
        article.image_url = "https://publisher.example/feed-preview.png"

    with storage_enabled.begin() as session:
        assert image_jobs.claim_image(session, job_id) is None
        old = session.get(ArticleImageJob, job_id)
        assert (old.status, old.outcome) == ("succeeded", "already_present")
        assert_unclaimed(old)
        successor = session.scalar(
            select(ArticleImageJob).where(
                ArticleImageJob.article_id == article_id, ArticleImageJob.status == "queued"
            )
        )
        assert successor.operation == "store"
        assert successor.image_url == "https://publisher.example/feed-preview.png"


@pytest.mark.parametrize("maintenance", ["recompression", "resize"])
def test_concurrent_thumbnail_requests_coalesce_without_replacing_the_served_asset(
    storage_enabled, maintenance
):
    request = (
        image_jobs.request_thumbnail_recompression
        if maintenance == "recompression"
        else image_jobs.request_thumbnail_resize
    )
    with storage_enabled.begin() as session:
        article = add_article(session)
        article_id = article.id
        original = managed_asset(
            article.image_url, version="v1" if maintenance == "recompression" else "v2"
        )
        article.managed_image = original

    def schedule(_):
        with storage_enabled.begin() as session:
            return request(session, article_id).id

    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = list(pool.map(schedule, range(3)))

    assert len(set(jobs)) == 1
    with storage_enabled() as session:
        assert list(session.scalars(select(ArticleImageJob.id))) == [jobs[0]]
        assert session.get(Article, article_id).managed_image == original
        assert_unclaimed(session.get(ArticleImageJob, jobs[0]))


@pytest.mark.parametrize("changed", ["source_url", "hash"])
def test_resize_retry_discards_checkpoint_from_a_replaced_original(storage_enabled, changed):
    with storage_enabled.begin() as session:
        article = add_article(session)
        article_id = article.id
        old_asset = managed_asset(article.image_url)
        article.managed_image = old_asset
        failed = image_jobs.request_thumbnail_resize(session, article.id)
        job_id = failed.id
        failed.status = "failed"
        failed.storage = {
            **failed.storage,
            "variants": old_asset["variants"] + [{"key": "old/480.webp", "width": 480}],
        }
        if changed == "source_url":
            article.image_url = "https://publisher.example/replaced.png"
        current_asset = managed_asset(
            article.image_url, digest="replacement" if changed == "hash" else "original"
        )
        article.managed_image = current_asset

    with storage_enabled.begin() as session:
        retry = image_jobs.retry_image(session, job_id)
        assert retry.id != job_id
        assert retry.storage == {**current_asset, "thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT}
        assert retry.image_url == session.get(Article, article_id).image_url
        assert session.get(ArticleImageJob, job_id).status == "failed"
        assert session.get(Article, article_id).managed_image == current_asset


@pytest.mark.parametrize("maintenance", ["recompression", "resize"])
def test_completed_thumbnail_upgrade_does_not_schedule_another_job(storage_enabled, maintenance):
    with storage_enabled.begin() as session:
        article = add_article(session)
        asset = managed_asset(article.image_url)
        if maintenance == "resize":
            asset["thumbnail_layout"] = ARTICLE_THUMBNAIL_LAYOUT
        article.managed_image = asset
        request = (
            image_jobs.request_thumbnail_recompression
            if maintenance == "recompression"
            else image_jobs.request_thumbnail_resize
        )

        assert request(session, article.id) is None
        assert list(session.scalars(select(ArticleImageJob.id))) == []
        assert article.managed_image == asset


@pytest.mark.parametrize(
    "backfill", [image_jobs.backfill_storage, image_jobs.backfill_thumbnail_sizes]
)
def test_disabled_storage_blocks_image_backfills_before_jobs_are_added(database, backfill):
    with database.begin() as session:
        with pytest.raises(OperationConflict, match="Enable image storage"):
            backfill(session, 10)
        assert list(session.scalars(select(ArticleImageJob.id))) == []


@pytest.mark.parametrize("policy_removed", [False, True])
def test_failed_upload_cleanup_retry_is_fenced_by_the_current_upload(
    storage_enabled, policy_removed
):
    with storage_enabled.begin() as session:
        product = add_product(session)
        product.merged_into_id = add_product(session).id
        account = PartnerAccount(name="Upload owner", tier="silver")
        session.add(account)
        session.flush()
        prefix = ad_image_prefix(account.id, product.id)
        policy = PartnerProductPromotion(
            account_id=account.id,
            product_id=product.id,
            ad_image_url="https://images.example/upload.png",
            managed_ad_image={
                "upload": True,
                "object_product_id": str(product.id),
                "object_prefix": prefix,
            },
        )
        session.add(policy)
        session.flush()
        failed = request_ad_image_cleanup(session, policy)
        failed.status = "failed"
        job_id = failed.id
        if policy_removed:
            session.delete(policy)

    with storage_enabled.begin() as session:
        retry = image_jobs.retry_image(session, job_id)
        if policy_removed:
            assert retry is None
            assert list(session.scalars(select(ArticleImageJob.id))) == [job_id]
        else:
            assert retry.id != job_id
            assert retry.storage == {"cleanup_upload": prefix}
            policy = session.get(PartnerProductPromotion, (retry.account_id, retry.product_id))
            policy.managed_ad_image = {}
            session.flush()
            assert image_jobs.claim_image(session, retry.id) is None
            assert (retry.status, retry.outcome) == ("succeeded", "already_present")
            assert_unclaimed(retry)
        assert session.get(ArticleImageJob, job_id).status == "failed"
