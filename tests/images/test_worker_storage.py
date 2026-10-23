"""Image workers preserve storage routing, completion, and cleanup ownership."""

import copy
import re
import uuid
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_aggregator import (
    image_storage_base,
    image_storage_tasks,
    image_tasks,
    logo_storage,
    product_image_storage,
    product_logo_storage,
)
from devfeed_aggregator import image_storage_tasks as storage
from devfeed_aggregator import image_tasks as worker
from devfeed_core import (
    image_jobs,
    logos,
    managed_images,
    partner_product_images,
    source_lifecycle,
    topic_logos,
)
from devfeed_core import partner_product_images as images
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.job_lifecycle import start_job
from devfeed_core.models import (
    ArticleImageJob,
    PartnerProduct,
    PartnerProductPromotion,
    Source,
    Topic,
)
from devfeed_core.object_storage import R2Storage
from devfeed_core.services import RecordNotFound

from tests.articles.test_workers import article as page_article
from tests.images.test_commands import article, asset, product, uploaded_asset
from tests.images.test_topic_logo_storage import picture
from tests.support.source_readiness import NOW, configuration, source
from tests.workers.test_automation_events_unit import topic

URL = "https://publisher.example/logo.png"


def running(**changes):
    row = ArticleImageJob(
        **{
            "id": uuid.uuid4(),
            "operation": "store",
            "article_id": uuid.uuid4(),
            "image_url": URL,
            "storage": {},
            "status": "queued",
            "attempts": 0,
            "created_at": NOW - timedelta(minutes=2),
            "available_at": NOW - timedelta(minutes=1),
            **changes,
        }
    )
    start_job(row, NOW - timedelta(seconds=30), 300)
    return row


class Factory:
    def __init__(self, session):
        self.session, self.open = session, False

    @contextmanager
    def begin(self):
        self.open = True
        try:
            yield self.session
        finally:
            self.open = False


@pytest.mark.parametrize(
    "kind", ["article", "source", "topic", "product-logo", "product", "promotion", "cleanup"]
)
def test_claimed_image_work_routes_to_the_owning_storage_service_after_releasing_the_claim_scope(
    monkeypatch, kind
):
    values = {}
    if kind != "article":
        values["article_id"] = None
        if kind in {"product-logo", "product", "promotion", "cleanup"}:
            values["product_id"] = uuid.uuid4()
        else:
            values[f"{kind}_id"] = uuid.uuid4()
        values["operation"] = {
            "source": "source-logo",
            "topic": "topic-logo",
            "product-logo": "product-logo",
            "product": "product-image",
            "promotion": "product-ad-image",
            "cleanup": "product-ad-image",
        }[kind]
    if kind in {"promotion", "cleanup"}:
        values.update(account_id=uuid.uuid4(), subject_revision=2)
    if kind == "cleanup":
        values["storage"] = {
            "cleanup_upload": partner_product_images.ad_image_prefix(
                values["account_id"], values["product_id"]
            )
        }
    if kind in {"product", "promotion", "cleanup"}:
        values["storage_version"] = "v2"
    elif kind in {"source", "topic", "product-logo"}:
        values["storage_version"] = logos.LOGO_VERSION
    row, session = running(**values), Mock()
    factory = Factory(session)
    monkeypatch.setattr(worker, "session_factory", lambda: factory)
    claim = Mock(return_value=(row, URL))
    monkeypatch.setattr(worker, "claim_image", claim)
    functions = {}
    for name in (
        "store_image",
        "store_logo",
        "store_product_image",
        "store_product_ad_image",
        "cleanup_product_ad_image",
    ):

        def check(*args, **kwargs):
            assert not factory.open

        boundary = Mock(side_effect=check)
        monkeypatch.setattr(storage, name, boundary)
        functions[name] = boundary
    token = row.lease_token
    worker.enrich_image(str(row.id))
    claim.assert_called_once_with(session, row.id)
    if kind == "article":
        functions["store_image"].assert_called_once_with(
            factory, row.id, token, row.article_id, URL
        )
    elif kind in {"source", "topic", "product-logo"}:
        model = {"source": Source, "topic": Topic, "product-logo": PartnerProduct}[kind]
        identifier = (
            row.source_id
            if kind == "source"
            else row.topic_id
            if kind == "topic"
            else row.product_id
        )
        functions["store_logo"].assert_called_once_with(
            factory, row.id, token, identifier, URL, model=model
        )
    elif kind == "product":
        functions["store_product_image"].assert_called_once_with(
            factory, row.id, token, row.product_id, URL
        )
    elif kind == "promotion":
        functions["store_product_ad_image"].assert_called_once_with(
            factory, row.id, token, row.account_id, row.product_id, URL
        )
    else:
        functions["cleanup_product_ad_image"].assert_called_once_with(
            factory, row.id, token, row.account_id, row.product_id
        )
    assert sum(boundary.call_count for boundary in functions.values()) == 1


def test_unclaimed_delivery_does_not_invoke_transport_or_storage(monkeypatch):
    factory = Factory(Mock())
    monkeypatch.setattr(worker, "session_factory", lambda: factory)
    monkeypatch.setattr(worker, "claim_image", Mock(return_value=None))
    fetch = Mock()
    monkeypatch.setattr(worker, "fetch_page", fetch)
    worker.enrich_image(str(uuid.uuid4()))
    fetch.assert_not_called()


def test_transport_failure_after_ownership_loss_does_not_alter_the_superseding_job(monkeypatch):
    session = Mock()
    row = running(operation="discover", image_url=None)
    factory = Factory(session)
    fetch = Mock(
        side_effect=FeedError(
            "private transport information", reason="http_error", status=503, retryable=True
        )
    )
    monkeypatch.setattr(worker, "fetch_page", fetch)
    owner = Mock(return_value=None)
    monkeypatch.setattr(worker, "owned_job", owner)
    before = copy.deepcopy((row.status, row.attempts, row.lease_token, row.lease_until))
    worker._enrich_claimed(
        factory, row.id, row.lease_token, row.article_id, "https://publisher.example/article", 0
    )
    assert before == (row.status, row.attempts, row.lease_token, row.lease_until)
    owner.assert_called_once_with(session, ArticleImageJob, row.id, row.lease_token)
    session.scalar.assert_not_called()


@pytest.mark.parametrize("defer", [False, True])
@pytest.mark.parametrize("state", ["empty", "complete", "changed", "owner-lost"])
def test_logo_original_publication_preserves_completed_replacements_and_resets_only_variant_budget(
    monkeypatch, defer, state
):
    current = source(logo_url=URL)
    row = running(
        article_id=None,
        source_id=current.id,
        operation="source-logo-refresh" if state == "complete" else "source-logo",
        storage_version=logos.LOGO_VERSION,
    )
    row.error = "Previous variant timeout"
    row.attempts = 2  # A second leased delivery retains the first transient error.
    original = asset(URL)
    original.pop("thumbnail_version")
    original["source_width"] = 96
    original["variants"] = []
    if state == "complete":
        current.managed_logo = {
            **original,
            "variants": [
                {"key": f"logos/old/{width}.webp", "width": width}
                for width in logos.logo_sizes(Source)
            ],
        }
    elif state == "changed":
        current.logo_url = "https://publisher.example/new-logo.png"
    before = copy.deepcopy(current.managed_logo)
    session = Mock()
    session.scalar.return_value = current
    owner = Mock(return_value=None if state == "owner-lost" else row)
    monkeypatch.setattr(storage, "owned_job", owner)
    monkeypatch.setattr(storage, "utcnow", lambda: NOW)
    token = row.lease_token
    result = storage.publish_logo_original(
        Factory(session), row.id, token, current.id, URL, original, defer=defer, model=Source
    )
    assert result is (state != "owner-lost")
    assert current.managed_logo == (original if state == "empty" else before)
    if result and defer:
        assert row.status == "queued" and row.attempts == 0 and row.available_at == NOW
        assert (
            row.error is None
            and row.lease_token is None
            and row.lease_until is None
            and row.dispatched_at is None
        )
    else:
        assert row.status == "running" and row.attempts == 2 and row.lease_token == token


@pytest.mark.parametrize("state", ["empty", "complete", "changed", "owner-lost"])
def test_product_original_publication_does_not_replace_an_existing_preview_or_changed_input(
    monkeypatch, state
):
    current = PartnerProduct(
        id=uuid.uuid4(),
        name="Developer tool",
        tagline="Build software",
        product_url=URL,
        status="pending",
        excluded=False,
        merged_into_id=None,
        managed_image={},
    )
    row = running(
        article_id=None, product_id=current.id, operation="product-image", storage_version="v2"
    )
    original = {**asset(URL), "page_url": current.product_url}
    original["variants"] = []
    if state == "complete":
        current.managed_image = {
            **original,
            "page_url": "https://product.example/previous",
            "source_url": "https://product.example/old-preview.png",
            "variants": [
                {"key": f"previous/{width}.webp", "width": width} for width in (320, 640, 960)
            ],
        }
    elif state == "changed":
        current.product_url = "https://publisher.example/new-product"
    before = copy.deepcopy(current.managed_image)
    session = Mock()
    session.scalar.return_value = current
    monkeypatch.setattr(
        storage, "owned_job", Mock(return_value=None if state == "owner-lost" else row)
    )
    assert storage.publish_product_image_original(
        Factory(session), row.id, row.lease_token, current.id, URL, original
    ) is (state != "owner-lost")
    assert current.managed_image == (original if state == "empty" else before)


@pytest.mark.parametrize(
    "kind", ["article", "source", "topic", "product-logo", "product", "promotion"]
)
def test_stale_asset_replacement_routes_to_the_same_subject_without_crossing_account_scope(
    monkeypatch, kind
):
    if kind == "article":
        subject, field, module, function = article(), "managed_image", image_jobs, "request_image"
    elif kind == "source":
        subject, field, module, function = source(), "managed_logo", logos, "request_logo"
    elif kind == "topic":
        subject, field, module, function = topic(), "managed_logo", logos, "request_logo"
    elif kind in {"product", "product-logo"}:
        subject = PartnerProduct(
            id=uuid.uuid4(),
            name="Developer tool",
            tagline="Build software",
            product_url="https://product.example",
            status="pending",
        )
        field = "managed_image" if kind == "product" else "managed_logo"
        module, function = (
            (partner_product_images, "request_product_image")
            if kind == "product"
            else (logos, "request_logo")
        )
    else:
        subject = PartnerProductPromotion(
            account_id=uuid.uuid4(),
            product_id=uuid.uuid4(),
            ad_image_revision=2,
            ad_image_url=URL,
            managed_ad_image={},
        )
        field, module, function = (
            "managed_ad_image",
            partner_product_images,
            "request_product_ad_image",
        )
    request, session = Mock(), Mock()
    monkeypatch.setattr(module, function, request)
    storage._replacement(session, type(subject), subject, asset_field=field)
    if kind == "promotion":
        request.assert_called_once_with(session, subject.account_id, subject.product_id)
    elif kind in {"article", "product"}:
        request.assert_called_once_with(session, subject.id, automatic=True)
    else:
        request.assert_called_once_with(session, type(subject), subject.id, automatic=True)
    session.flush.assert_called_once()


@pytest.fixture
def enabled_storage(monkeypatch):
    settings = configuration(image_storage_enabled=True, image_public_url="https://images.example")
    for module in (image_jobs, image_tasks, logos, managed_images, partner_product_images):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(image_tasks, "utcnow", lambda: NOW)


def test_failed_product_preview_retry_admits_a_new_job_without_rewriting_failed_history(
    enabled_storage,
):
    current = product()
    previous = ArticleImageJob(
        id=uuid.uuid4(),
        product_id=current.id,
        operation="product-image",
        image_url=current.product_url,
        storage={},
        storage_version="v2",
        status="failed",
        attempts=3,
        error="Image storage failed: transport_error",
        created_at=NOW - timedelta(hours=1),
        available_at=NOW - timedelta(minutes=2),
        finished_at=NOW - timedelta(minutes=1),
    )
    before = copy.deepcopy(
        (previous.status, previous.finished_at, previous.error, previous.storage)
    )
    session = Mock()
    session.get.return_value = previous
    session.scalar.side_effect = [current, None]

    requested = image_jobs.retry_image(session, previous.id)

    assert requested is not previous and requested.product_id == current.id
    assert requested.article_id is None and requested.operation == "product-image"
    assert requested.image_url == current.product_url and requested.storage_version == "v2"
    assert (previous.status, previous.finished_at, previous.error, previous.storage) == before
    session.add.assert_called_once_with(requested)


def test_successful_page_image_lookup_requests_storage_only_after_owned_completion(
    enabled_storage, monkeypatch
):
    current = page_article()
    row = running(
        article_id=current.id,
        operation="discover",
        image_url=None,
        storage={"input_url": current.canonical_url},
    )
    session = Mock()
    session.scalar.side_effect = [current.id, current.id]
    factory = Factory(session)
    token = row.lease_token
    monkeypatch.setattr(image_tasks, "owned_job", Mock(return_value=row))

    def fetch(url):
        assert not factory.open
        return FetchResult(
            200,
            b'<html><meta property="og:image" content="https://publisher.example/cover.png"></html>',
            url,
            content_type="text/html",
        )

    monkeypatch.setattr(image_tasks, "fetch_page", fetch)

    def storage_request(session_arg, article_id, **options):
        assert factory.open and session_arg is session and article_id == current.id
        assert options == {"automatic": True}
        assert row.status == "succeeded" and row.outcome == "found"
        assert row.lease_token is row.lease_until is None

    request = Mock(side_effect=storage_request)
    monkeypatch.setattr(image_tasks, "request_image", request)

    image_tasks._enrich_claimed(factory, row.id, token, current.id, current.canonical_url, 0)

    assert row.image_url == "https://publisher.example/cover.png" and row.method == "og:image"
    assert row.attempts == 1
    request.assert_called_once_with(session, current.id, automatic=True)


@pytest.mark.parametrize("backfill", [False, True])
def test_topic_logo_entrypoints_emit_topic_jobs_with_the_declared_logo_identity(
    enabled_storage, backfill
):
    current = topic(logo_url="https://python.example/logo.png")
    session = Mock()
    session.new = []
    session.scalar.side_effect = [current, None, *([None] if backfill else [])]
    session.scalars.return_value.all.return_value = [current.id]

    if backfill:
        [requested] = topic_logos.backfill_topic_logos(session, limit=1)
    else:
        requested = topic_logos.request_topic_logo(session, current.id)

    assert requested.topic_id == current.id and requested.operation == "topic-logo"
    assert requested.article_id is requested.source_id is requested.product_id is None
    assert (
        requested.image_url == current.logo_url and requested.storage_version == logos.LOGO_VERSION
    )
    lookup = session.scalar.call_args_list[0].args[0]
    assert lookup.column_descriptions[0]["entity"] is Topic
    if backfill:
        selection = session.scalars.call_args.args[0]
        assert selection.column_descriptions[0]["entity"] is Topic
    session.add.assert_called_once_with(requested)


def test_empty_successful_product_page_cannot_be_uploaded_as_a_preview(monkeypatch):
    client = Mock(spec=R2Storage)
    monkeypatch.setattr(
        product_image_storage,
        "fetch_page",
        Mock(
            return_value=FetchResult(200, b"", "https://product.example/", content_type="text/html")
        ),
    )

    with pytest.raises(FeedError) as failure:
        product_image_storage.ProductImageOriginalStore(client).store("https://product.example/")

    assert failure.value.reason == "invalid_product_page"
    client.put.assert_not_called()


def test_product_logo_exhausts_only_its_bounded_declared_and_same_origin_icon_candidates(
    monkeypatch,
):
    url = "https://product.example/"
    client = Mock(spec=R2Storage)
    monkeypatch.setattr(
        product_logo_storage,
        "fetch_page",
        Mock(
            return_value=FetchResult(
                200,
                b'<link rel="icon" href="/icon.png">'
                b'<link rel="apple-touch-icon" href="/touch.png">',
                url,
                content_type="text/html",
            )
        ),
    )
    fetch = Mock(
        side_effect=lambda source: FetchResult(
            200, b"not an image", source, content_type="image/png"
        )
    )
    monkeypatch.setattr(logo_storage, "fetch_topic_logo", fetch)

    with pytest.raises(FeedError) as failure:
        product_logo_storage.ProductLogoOriginalStore(client).store(url)

    assert failure.value.reason == "invalid_image"
    assert [call.args[0] for call in fetch.call_args_list] == [
        url + "touch.png",
        url + "icon.png",
        url + "favicon.ico",
    ]
    client.put.assert_not_called()


@pytest.mark.parametrize("operation", ["dispatch", "recompress", "resize"])
def test_missing_image_job_or_article_refuses_dispatch_and_maintenance_without_new_work(operation):
    session = Mock()
    session.scalar.return_value = None
    command = {
        "dispatch": image_jobs.prepare_image_dispatch,
        "recompress": image_jobs.request_thumbnail_recompression,
        "resize": image_jobs.request_thumbnail_resize,
    }[operation]

    with pytest.raises(RecordNotFound):
        command(session, uuid.uuid4())

    session.add.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.parametrize(
    "command", [image_jobs.backfill_storage, image_jobs.backfill_thumbnail_sizes]
)
@pytest.mark.parametrize("limit", [0, 501])
def test_unbounded_storage_backfill_is_rejected_before_any_database_work(command, limit):
    session = Mock()
    with pytest.raises(ValueError, match="between 1 and 500"):
        command(session, limit)
    assert session.mock_calls == []


def test_explicit_retry_of_failed_discovery_captures_a_new_refresh_without_rewriting_history(
    monkeypatch,
):
    settings = configuration(image_storage_enabled=True, image_public_url="https://images.example")
    monkeypatch.setattr(image_jobs, "get_settings", lambda: settings)
    current = page_article()
    failed = ArticleImageJob(
        id=uuid.uuid4(),
        article_id=current.id,
        operation="discover",
        image_url=None,
        storage={"input_url": current.canonical_url},
        status="failed",
        attempts=3,
        error="Image lookup failed: transport_error",
        created_at=NOW - timedelta(minutes=5),
        available_at=NOW - timedelta(minutes=5),
        finished_at=NOW - timedelta(minutes=1),
    )
    before = copy.deepcopy((failed.status, failed.error, failed.storage, failed.finished_at))
    session = Mock()
    session.get.return_value = failed
    session.scalar.side_effect = [current, None]

    requested = image_jobs.retry_image(session, failed.id, refresh=True)

    assert requested is not failed and requested.article_id == current.id
    assert requested.operation == "discover" and requested.storage == {
        "article_refresh": True,
        "input_url": current.canonical_url,
        "previous_image_url": None,
    }
    assert (failed.status, failed.error, failed.storage, failed.finished_at) == before
    session.add.assert_called_once_with(requested)


@pytest.mark.parametrize("model", [Source, Topic, PartnerProduct])
def test_logo_original_handoff_then_variants_preserve_subject_identity_and_resume_from_storage(
    monkeypatch, model
):
    settings = configuration(
        image_storage_enabled=True,
        image_public_url="https://images.example",
        imgproxy_url="http://imgproxy.internal:8080",
        imgproxy_key="ab" * 32,
        imgproxy_salt="cd" * 32,
    )
    monkeypatch.setattr(image_storage_base, "get_settings", lambda: settings)
    monkeypatch.setattr(image_storage_tasks, "utcnow", lambda: NOW)
    icon = "https://publisher.example/logo.png"
    current = (
        source(logo_url=icon)
        if model is Source
        else topic(logo_url=icon)
        if model is Topic
        else product()
    )
    origin = logos.logo_source(current)
    source_field = {Source: "source_id", Topic: "topic_id", PartnerProduct: "product_id"}[model]
    job = running(
        article_id=None,
        **{source_field: current.id},
        operation={Source: "source-logo", Topic: "topic-logo", PartnerProduct: "product-logo"}[
            model
        ],
        image_url=origin,
        storage_version=logos.LOGO_VERSION,
    )
    session = Mock()
    session.get.return_value = job
    session.scalar.return_value = current
    factory = Mock(return_value=nullcontext(session))
    factory.begin.return_value = nullcontext(session)
    monkeypatch.setattr(image_storage_tasks, "owned_job", Mock(return_value=job))
    objects = {}
    client = Mock(spec=R2Storage)
    client.exists.side_effect = objects.__contains__
    client.put.side_effect = lambda key, body, **_options: objects.__setitem__(key, body)
    monkeypatch.setattr(image_storage_tasks, "storage_client", lambda: nullcontext(client))
    fetch_icon = Mock(return_value=FetchResult(200, picture(), icon, content_type="image/png"))
    monkeypatch.setattr(logo_storage, "fetch_topic_logo", fetch_icon)
    monkeypatch.setattr(
        product_logo_storage,
        "fetch_page",
        Mock(return_value=FetchResult(200, f'<link rel="icon" href="{icon}">'.encode(), origin)),
    )
    reconcile = Mock()
    monkeypatch.setattr(source_lifecycle, "reconcile_source_processing", reconcile)

    image_storage_tasks.store_logo(
        factory, job.id, job.lease_token, current.id, origin, model=model
    )

    original = copy.deepcopy(current.managed_logo)
    assert job.status == "queued" and job.attempts == 0 and job.available_at == NOW
    assert job.lease_token is job.lease_until is None
    assert original["source_url"] == origin and original["version"] == logos.LOGO_VERSION
    assert original["variants"] == [] and objects[original["original_key"]]
    assert len(objects) == 1

    def transformed(_self, url):
        match = re.search(r"/rs:fit:(\d+):", url)
        assert match is not None
        width = int(match[1])
        return picture(width, width)

    monkeypatch.setattr(image_storage_base.ImgproxyTransform, "fetch", transformed)
    start_job(job, NOW, 300)
    token = job.lease_token
    image_storage_tasks.store_logo(factory, job.id, token, current.id, origin, model=model)

    assert job.status == "succeeded" and job.outcome == "found"
    assert job.lease_token is job.lease_until is None
    assert logos.logo_current(current)
    assert current.managed_logo["original_key"] == original["original_key"]
    assert current.managed_logo["hash"] == original["hash"]
    assert {row["width"] for row in current.managed_logo["variants"]} == set(
        logos.logo_sizes(model)
    )
    assert fetch_icon.call_count == 1
    assert len(objects) == 1 + len(logos.logo_sizes(model))
    if model is Source:
        reconcile.assert_called_once_with(session, current, automatic=True)
    else:
        reconcile.assert_not_called()


@pytest.mark.parametrize("result", ["success", "transport-failure", "lease-lost"])
def test_cleanup_keeps_policy_until_the_owned_upload_namespace_is_deleted(monkeypatch, result):
    account_id, product_id = uuid.uuid4(), uuid.uuid4()
    prefix = images.ad_image_prefix(account_id, product_id)
    product = PartnerProduct(
        id=product_id,
        name="Former product identity",
        tagline="Developer tool",
        product_url="https://product.example/",
        status="withdrawn",
        merged_into_id=uuid.uuid4(),
    )
    upload = uploaded_asset(account_id, product_id)
    policy = PartnerProductPromotion(
        account_id=account_id,
        product_id=product_id,
        ad_image_revision=2,
        ad_image_url=upload["source_url"],
        managed_ad_image=upload,
    )
    before = copy.deepcopy(policy.managed_ad_image)
    row = running(
        article_id=None,
        product_id=product_id,
        account_id=account_id,
        subject_revision=2,
        operation="product-ad-image",
        image_url=policy.ad_image_url,
        storage_version="v2",
        storage={"cleanup_upload": prefix},
    )
    session = Mock()
    session.scalar.side_effect = [product_id, policy]
    session.get.return_value = product
    owner = Mock(return_value=None if result == "lease-lost" else row)
    monkeypatch.setattr(storage, "owned_job", owner)
    monkeypatch.setattr(storage, "utcnow", lambda: NOW)
    client, connect = Mock(), Mock()
    connect.return_value = nullcontext(client)
    monkeypatch.setattr(storage, "storage_client", connect)
    if result == "transport-failure":
        client.delete.side_effect = [None, OSError("signed private credential")]
    storage.cleanup_product_ad_image(
        Factory(session), row.id, row.lease_token, account_id, product_id
    )
    if result == "success":
        assert [call.args[0] for call in client.delete.call_args_list] == [
            f"{prefix}/{width}.webp" for width in images.AD_IMAGE_SIZES
        ]
        assert (
            policy.managed_ad_image == {} and row.status == "succeeded" and row.outcome == "found"
        )
        assert row.lease_token is None
    else:
        assert policy.managed_ad_image == before
        if result == "transport-failure":
            assert row.status == "queued" and row.error == "Image cleanup failed: OSError"
            assert row.lease_token is None and row.available_at > NOW
            assert "private" not in row.error
        else:
            assert row.status == "running"
            connect.assert_not_called()


@pytest.mark.parametrize("kind", ["article", "product", "custom"])
def test_storage_wrappers_bind_the_right_subject_and_original_variant_policies(monkeypatch, kind):
    operation = Mock()
    monkeypatch.setattr(storage, "_store_asset", operation)
    factory, job_id, token, subject, account = (
        object(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    url = "https://publisher.example/image.png"
    if kind == "article":
        storage.store_image(factory, job_id, token, subject, url)
        from devfeed_core.models import Article

        assert operation.call_args.kwargs["model"] is Article
        assert operation.call_args.kwargs["original_store"] is storage.ImageOriginalStore
        assert operation.call_args.kwargs["variant_store"] is storage.ArticleThumbnailVariantStore
        assert operation.call_args.kwargs["source_field"] == "image_url"
        assert operation.call_args.args == (factory, job_id, token, subject, url)
    elif kind == "product":
        storage.store_product_image(factory, job_id, token, subject, url)
        assert operation.call_args.kwargs["model"] is PartnerProduct
        assert (
            operation.call_args.kwargs["original_store"]
            is product_image_storage.ProductImageOriginalStore
        )
        assert operation.call_args.kwargs["source_field"] == "product_url"
        assert operation.call_args.args == (factory, job_id, token, subject, url)
    else:
        storage.store_product_ad_image(factory, job_id, token, account, subject, url)
        assert operation.call_args.kwargs["model"] is PartnerProductPromotion
        assert operation.call_args.kwargs["original_store"] is storage.ImageOriginalStore
        assert operation.call_args.kwargs["asset_field"] == "managed_ad_image"
        assert operation.call_args.kwargs["source_field"] == "ad_image_url"
        assert operation.call_args.args == (factory, job_id, token, (account, subject), url)
    if kind != "article":
        assert operation.call_args.kwargs["variant_store"] is storage.ThumbnailVariantStore
    assert operation.call_args.kwargs["method"] == "r2-imgproxy"
