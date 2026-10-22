"""Image orchestration over repository boundaries, without simulating SQL or commits."""

import copy
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from devfeed_aggregator import image_storage_tasks as tasks
from devfeed_core import image_jobs, logos, partner_product_images, source_lifecycle
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.job_lifecycle import start_job
from devfeed_core.managed_images import ARTICLE_THUMBNAIL_LAYOUT, IMAGE_VERSION, THUMBNAIL_VERSION
from devfeed_core.models import (
    Article,
    ArticleImageJob,
    PartnerProduct,
    PartnerProductPromotion,
    Source,
    Topic,
)

NOW = datetime(2026, 10, 22, tzinfo=UTC)
URL = "https://publisher.example/image.png"


def original_asset():
    return {
        "source_url": URL,
        "version": IMAGE_VERSION,
        "original_key": "originals/content-hash.png",
        "variants": [],
        "thumbnail_version": THUMBNAIL_VERSION,
    }


@pytest.fixture
def runtime(monkeypatch):
    """Preloaded ORM facts with explicit repository admission/ownership results."""
    article = Article(
        id=uuid.uuid4(),
        title="Image contract",
        canonical_url="https://publisher.example/article",
        url_hash="article-contract",
        publication_status="unpublished",
        image_url=URL,
        managed_image={},
    )
    job = ArticleImageJob(
        id=uuid.uuid4(),
        article_id=article.id,
        operation="store",
        image_url=URL,
        storage={},
        status="queued",
        attempts=0,
        created_at=NOW,
        available_at=NOW,
        dispatched_at=NOW,
    )
    token = start_job(job, NOW, 180)
    session = Mock()
    session.get.return_value = job
    factory = Mock(return_value=nullcontext(session))
    factory.begin.return_value = nullcontext(session)
    owned = Mock(return_value=job)
    lookup = Mock(return_value=article)
    monkeypatch.setattr(tasks, "owned_job", owned)
    monkeypatch.setattr(tasks, "_lock_subject", lookup)
    clock = SimpleNamespace(now=NOW)
    monkeypatch.setattr(tasks, "utcnow", lambda: clock.now)
    client = Mock()
    monkeypatch.setattr(tasks, "storage_client", lambda: nullcontext(client))
    original = Mock()
    original.store.return_value = original_asset()
    variants = Mock()
    variants.sizes.return_value = [400, 800]
    variants.store.side_effect = lambda asset, size: {"width": size, "key": f"thumb/{size}.webp"}
    original_factory = Mock(return_value=original)
    variant_factory = Mock(return_value=variants)
    replacement = Mock()
    monkeypatch.setattr(tasks, "_replacement", replacement)
    value = SimpleNamespace(
        subject=article,
        job=job,
        token=token,
        session=session,
        factory=factory,
        owned=owned,
        lookup=lookup,
        client=client,
        original=original,
        variants=variants,
        original_factory=original_factory,
        variant_factory=variant_factory,
        replacement=replacement,
        model=Article,
        source_field="image_url",
        asset_field="managed_image",
        clock=clock,
    )

    def store():
        identifier = (
            (value.subject.account_id, value.subject.product_id)
            if value.model is PartnerProductPromotion
            else value.subject.id
        )
        tasks._store_asset(
            factory,
            job.id,
            value.token,
            identifier,
            URL,
            model=value.model,
            source_field=value.source_field,
            asset_field=value.asset_field,
            original_store=original_factory,
            variant_store=variant_factory,
            method="r2-imgproxy" if value.asset_field != "managed_logo" else "r2-logo",
        )

    value.store = store
    return value


def use_catalog(runtime, kind):
    job = runtime.job
    job.article_id = None
    if kind == "product":
        subject = PartnerProduct(
            id=uuid.uuid4(),
            name="Developer tool",
            tagline="Build software",
            product_url=URL,
            status="pending",
            managed_image={},
            managed_logo={},
        )
        job.product_id, job.operation = subject.id, "product-image"
        runtime.model, runtime.source_field = PartnerProduct, "product_url"
    elif kind == "promotion":
        subject = PartnerProductPromotion(
            account_id=uuid.uuid4(),
            product_id=uuid.uuid4(),
            ad_image_url=URL,
            ad_image_revision=2,
            managed_ad_image={},
        )
        job.account_id, job.product_id = subject.account_id, subject.product_id
        job.subject_revision, job.operation = 2, "product-ad-image"
        runtime.model = PartnerProductPromotion
        runtime.source_field, runtime.asset_field = "ad_image_url", "managed_ad_image"
    else:
        subject = Source(
            id=uuid.uuid4(),
            name="Publisher",
            feed_url="https://publisher.example/feed.xml",
            source_type="publisher",
            approval_status="pending",
            enabled=False,
            logo_url=URL,
            managed_logo={},
        )
        job.source_id, job.operation = subject.id, "source-logo"
        runtime.model = Source
        runtime.source_field, runtime.asset_field = "logo_url", "managed_logo"
        runtime.original.store.return_value = {
            "source_url": URL,
            "version": logos.LOGO_VERSION,
            "original_key": "originals/content-hash.png",
            "variants": [],
        }
        runtime.variants.sizes.return_value = logos.SOURCE_LOGO_SIZES
    if kind == "product":
        runtime.original.store.return_value = {**original_asset(), "page_url": URL}
    runtime.subject = subject
    runtime.lookup.return_value = subject
    return subject


def reopen_receipt(runtime):
    receipt = {
        "kind": "reopen_review",
        "id": str(uuid.uuid4()),
        "actor": {"subject": "editor"},
        "requested_at": NOW.isoformat(),
        "manual_review": True,
        "required": ["profile", "branding"],
    }
    runtime.subject.created_at = NOW - timedelta(days=2)
    runtime.subject.reviewed_at = NOW - timedelta(days=1)
    runtime.subject.reviewed_by = "editor"
    runtime.subject.review_note = "Previously rejected; review reopened."
    runtime.subject.processing_request = copy.deepcopy(receipt)
    return receipt


@pytest.mark.parametrize("refresh", [False, True])
def test_article_exposes_new_asset_only_after_all_sizes_finish(runtime, refresh):
    prior = (
        {**original_asset(), "variants": [{"width": 400, "key": "previous/400.webp"}]}
        if refresh
        else {}
    )
    runtime.subject.managed_image = copy.deepcopy(prior)
    runtime.job.storage = {"article_refresh": True} if refresh else {}
    checkpoints = []

    def resize(asset, size):
        assert runtime.subject.managed_image == prior
        assert bool(runtime.job.storage.get("article_refresh")) is refresh
        checkpoints.append(copy.deepcopy(runtime.job.storage))
        return {"width": size, "key": f"thumb/{size}.webp"}

    runtime.variants.store.side_effect = resize
    runtime.store()

    runtime.original.store.assert_called_once_with(URL)
    assert [[v["width"] for v in asset["variants"]] for asset in checkpoints] == [[], [400]]
    assert [v["width"] for v in runtime.subject.managed_image["variants"]] == [400, 800]
    assert runtime.subject.managed_image["thumbnail_layout"] == ARTICLE_THUMBNAIL_LAYOUT
    assert "source_pipeline_request" not in runtime.job.storage
    assert "article_refresh" not in runtime.job.storage
    assert runtime.job.status == "succeeded" and runtime.job.outcome == "found"
    assert runtime.job.lease_token is None and runtime.job.finished_at == NOW


def test_retry_retains_uploaded_sizes_and_resumes_without_refetching_original(runtime, caplog):
    def resize(asset, size):
        if size == 800:
            raise FeedError(
                "signed URL secret=do-not-persist",
                reason="image_proxy_unavailable",
                retryable=True,
                status=503,
                retry_after=120,
            )
        return {"width": size, "key": f"thumb/{size}.webp"}

    runtime.variants.store.side_effect = resize
    runtime.store()
    assert runtime.job.status == "queued" and runtime.subject.managed_image == {}
    assert runtime.job.available_at == NOW + timedelta(seconds=120)
    assert runtime.job.error == "Image storage failed: image_proxy_unavailable"
    assert runtime.job.http_status == 503 and runtime.job.lease_token is None
    assert [v["width"] for v in runtime.job.storage["variants"]] == [400]
    assert "do-not-persist" not in caplog.text

    runtime.token = start_job(runtime.job, NOW + timedelta(seconds=120), 180)
    runtime.clock.now = NOW + timedelta(seconds=120)
    runtime.variants.store.reset_mock(side_effect=True)
    runtime.variants.store.side_effect = lambda asset, size: {
        "width": size,
        "key": f"thumb/{size}.webp",
    }
    runtime.store()
    runtime.original.store.assert_called_once_with(URL)
    assert runtime.variants.store.call_count == 1
    assert runtime.variants.store.call_args.args[1] == 800
    assert runtime.job.status == "succeeded" and runtime.job.attempts == 2
    assert runtime.job.finished_at == NOW + timedelta(seconds=120)
    assert [v["width"] for v in runtime.subject.managed_image["variants"]] == [400, 800]


@pytest.mark.parametrize("admission", ["missing", "queued", "stale_lease"])
def test_invalid_delivery_does_no_remote_image_work(runtime, admission):
    if admission == "missing":
        runtime.session.get.return_value = None
    elif admission == "queued":
        runtime.job.status = "queued"
        runtime.job.lease_token = runtime.job.lease_until = None
    else:
        runtime.job.lease_token = uuid.uuid4()
    runtime.store()
    runtime.original_factory.assert_not_called()
    runtime.variant_factory.assert_not_called()


@pytest.mark.parametrize("phase", ["original_checkpoint", "variant_checkpoint", "publication"])
def test_repository_rejection_stops_further_uploads_and_publication(runtime, phase):
    runtime.owned.side_effect = {
        "original_checkpoint": [None],
        "variant_checkpoint": [runtime.job, None],
        "publication": [runtime.job, runtime.job, runtime.job, None],
    }[phase]
    runtime.store()
    assert runtime.subject.managed_image == {}
    assert runtime.job.status == "running" and runtime.job.outcome is None
    assert (
        runtime.variants.store.call_count
        == {
            "original_checkpoint": 0,
            "variant_checkpoint": 1,
            "publication": 2,
        }[phase]
    )


@pytest.mark.parametrize("kind", ["article", "promotion"])
def test_changed_input_finishes_obsolete_work_without_overwriting_current_asset(runtime, kind):
    if kind == "promotion":
        use_catalog(runtime, kind)
    prior = {"variants": [{"width": 400, "key": "previous/400.webp"}]}
    setattr(runtime.subject, runtime.asset_field, copy.deepcopy(prior))

    def resize(asset, size):
        if kind == "promotion":
            runtime.subject.ad_image_revision = 3
        else:
            runtime.subject.image_url = "https://publisher.example/new-image.png"
        return {"width": size, "key": f"thumb/{size}.webp"}

    runtime.variants.store.side_effect = resize
    runtime.store()
    assert getattr(runtime.subject, runtime.asset_field) == prior
    assert runtime.job.status == "succeeded" and runtime.job.outcome == "already_present"
    runtime.replacement.assert_called_once_with(
        runtime.session, runtime.model, runtime.subject, asset_field=runtime.asset_field
    )


def test_imported_logo_defers_variants_to_separate_delivery(runtime, monkeypatch):
    use_catalog(runtime, "source")
    marker = reopen_receipt(runtime)
    runtime.job.storage = {"source_pipeline_request": marker}
    publish = Mock(return_value=True)
    monkeypatch.setattr(tasks, "publish_logo_original", publish)
    runtime.store()
    assert runtime.job.storage["original_key"] == "originals/content-hash.png"
    assert runtime.job.storage["source_pipeline_request"] == marker
    assert runtime.subject.managed_logo == {}
    runtime.variants.sizes.assert_not_called()
    assert publish.call_args.kwargs == {"defer": True, "model": Source}


def test_source_logo_resume_preserves_pipeline_receipt_and_reconciles_only_after_completion(
    runtime, monkeypatch
):
    use_catalog(runtime, "source")
    marker = reopen_receipt(runtime)
    runtime.job.storage = {
        **runtime.original.store.return_value,
        "source_pipeline_request": marker,
    }
    publish = Mock(return_value=True)
    reconcile = Mock()
    monkeypatch.setattr(tasks, "publish_logo_original", publish)
    monkeypatch.setattr(source_lifecycle, "reconcile_source_processing", reconcile)
    runtime.store()
    runtime.original.store.assert_not_called()
    assert publish.call_args.kwargs == {"defer": False, "model": Source}
    assert runtime.job.storage["source_pipeline_request"] == marker
    assert runtime.job.status == "succeeded"
    reconcile.assert_called_once_with(runtime.session, runtime.subject, automatic=True)
    assert [v["width"] for v in runtime.subject.managed_logo["variants"]] == list(
        logos.SOURCE_LOGO_SIZES
    )


@pytest.mark.parametrize("kind", ["source", "product"])
def test_original_publication_repository_rejection_stops_variant_work(runtime, monkeypatch, kind):
    use_catalog(runtime, kind)
    runtime.job.storage = copy.deepcopy(runtime.original.store.return_value)
    boundary = "publish_logo_original" if kind == "source" else "publish_product_image_original"
    monkeypatch.setattr(tasks, boundary, Mock(return_value=False))
    runtime.store()
    runtime.variants.store.assert_not_called()
    assert runtime.job.status == "running"


@pytest.mark.parametrize("kind", ["source", "product", "promotion"])
def test_remote_failure_after_input_change_schedules_replacement_instead_of_retry(
    runtime, monkeypatch, kind
):
    subject = use_catalog(runtime, kind)

    def failed_import(url):
        setattr(subject, runtime.source_field, "https://publisher.example/replacement.png")
        raise RuntimeError("signed upstream credential")

    runtime.original.store.side_effect = failed_import
    runtime.store()
    assert runtime.job.status == "succeeded" and runtime.job.outcome == "already_present"
    assert runtime.job.error is None
    runtime.replacement.assert_called_once()


def test_product_without_page_image_is_terminal_not_found(runtime):
    use_catalog(runtime, "product")
    runtime.original.store.side_effect = FeedError(
        "no preview metadata", reason="product_image_not_found", retryable=False, status=200
    )
    runtime.store()
    assert runtime.job.status == "succeeded" and runtime.job.outcome == "not_found"
    assert runtime.job.error is None and runtime.subject.managed_image == {}


@pytest.mark.parametrize("retryable,expected", [(False, "failed"), (True, "queued")])
def test_transport_failure_uses_declared_retry_policy(runtime, retryable, expected):
    runtime.original.store.side_effect = FeedError(
        "upstream detail", reason="http_error", retryable=retryable, status=429, retry_after=90
    )
    runtime.store()
    assert runtime.job.status == expected and runtime.job.http_status == 429
    assert runtime.job.error == "Image storage failed: http_error"
    assert runtime.job.lease_token is None


def test_unclassified_storage_exception_records_only_safe_type(runtime, caplog):
    runtime.original.store.side_effect = RuntimeError("https://signed.example/?token=secret")
    runtime.store()
    assert runtime.job.error == "Image storage failed: RuntimeError"
    assert runtime.job.status == "queued" and runtime.job.http_status is None
    assert "token=secret" not in caplog.text


@pytest.mark.parametrize("missing", ["subject", "ownership"])
def test_failure_after_subject_or_lease_removal_cannot_finish_foreign_job(runtime, missing):
    runtime.original.store.side_effect = RuntimeError("remote error")
    if missing == "subject":
        runtime.lookup.return_value = None
    else:
        runtime.owned.return_value = None
    runtime.store()
    assert runtime.job.status == "running" and runtime.job.error is None


@pytest.mark.parametrize(
    "kind", ["article", "product", "promotion", "source", "topic", "product-logo"]
)
def test_obsolete_work_requests_correct_automatic_replacement(monkeypatch, kind):
    requests = {name: Mock() for name in ["article", "product", "promotion", "logo"]}
    monkeypatch.setattr(image_jobs, "request_image", requests["article"])
    monkeypatch.setattr(partner_product_images, "request_product_image", requests["product"])
    monkeypatch.setattr(partner_product_images, "request_product_ad_image", requests["promotion"])
    monkeypatch.setattr(logos, "request_logo", requests["logo"])
    session = Mock()
    subject = SimpleNamespace(id=uuid.uuid4(), account_id=uuid.uuid4(), product_id=uuid.uuid4())
    model = {
        "article": Article,
        "product": PartnerProduct,
        "promotion": PartnerProductPromotion,
        "source": Source,
        "topic": Topic,
        "product-logo": PartnerProduct,
    }[kind]
    # This test has no runtime fixture: exercise the actual replacement dispatcher.
    tasks._replacement(
        session,
        model,
        subject,
        asset_field="managed_image" if kind == "product" else "managed_logo",
    )
    session.flush.assert_called_once_with()
    if kind == "promotion":
        requests[kind].assert_called_once_with(session, subject.account_id, subject.product_id)
    elif kind in {"article", "product"}:
        requests[kind].assert_called_once_with(session, subject.id, automatic=True)
    else:
        requests["logo"].assert_called_once_with(session, model, subject.id, automatic=True)
    assert sum(request.call_count for request in requests.values()) == 1


@pytest.mark.parametrize("failure", [False, True])
def test_merged_product_cleanup_deletes_only_owned_upload_variants(runtime, failure):
    policy = use_catalog(runtime, "promotion")
    product = PartnerProduct(
        id=policy.product_id,
        name="Merged developer tool",
        tagline="Build software",
        product_url="https://tool.example/",
        status="withdrawn",
        merged_into_id=uuid.uuid4(),
    )
    prefix = partner_product_images.ad_image_prefix(policy.account_id, policy.product_id)
    policy.managed_ad_image = {
        "upload": True,
        "object_product_id": str(policy.product_id),
        "object_prefix": prefix,
    }
    runtime.job.storage = {"cleanup_upload": prefix}
    runtime.session.get.return_value = product
    if failure:
        runtime.client.delete.side_effect = RuntimeError("signed URL secret")
    tasks.cleanup_product_ad_image(
        runtime.factory, runtime.job.id, runtime.token, policy.account_id, policy.product_id
    )
    if failure:
        assert runtime.job.status == "queued"
        assert runtime.job.error == "Image cleanup failed: RuntimeError"
        assert policy.managed_ad_image["object_prefix"] == prefix
    else:
        assert runtime.client.delete.call_args_list == [
            call(f"{prefix}/{size}.webp") for size in partner_product_images.AD_IMAGE_SIZES
        ]
        assert policy.managed_ad_image == {}
        assert runtime.job.status == "succeeded" and runtime.job.outcome == "found"


@pytest.mark.parametrize("reason", ["not_merged", "different_upload", "missing_product"])
def test_cleanup_keeps_current_or_unowned_uploads(runtime, reason):
    policy = use_catalog(runtime, "promotion")
    product = PartnerProduct(
        id=policy.product_id,
        name="Merged developer tool",
        tagline="Build software",
        product_url="https://tool.example/",
        status="withdrawn",
        merged_into_id=uuid.uuid4(),
    )
    prefix = partner_product_images.ad_image_prefix(policy.account_id, policy.product_id)
    policy.managed_ad_image = {
        "upload": True,
        "object_product_id": str(policy.product_id),
        "object_prefix": prefix,
    }
    runtime.job.storage = {"cleanup_upload": prefix}
    if reason == "not_merged":
        product.merged_into_id = None
        product.status = "pending"
    elif reason == "different_upload":
        runtime.job.storage = {"cleanup_upload": "another-account/upload"}
    runtime.session.get.return_value = None if reason == "missing_product" else product
    tasks.cleanup_product_ad_image(
        runtime.factory, runtime.job.id, runtime.token, policy.account_id, policy.product_id
    )
    runtime.client.delete.assert_not_called()
    assert policy.managed_ad_image["object_prefix"] == prefix
    assert runtime.job.outcome == "already_present"
