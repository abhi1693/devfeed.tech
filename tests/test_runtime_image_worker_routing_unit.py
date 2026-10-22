"""Image worker routing and partial-publication boundaries, without SQL/lock simulation."""

import copy
import uuid
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_aggregator import image_storage_tasks as storage
from devfeed_aggregator import image_tasks as worker
from devfeed_core import image_jobs, logos, partner_product_images
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.job_lifecycle import start_job
from devfeed_core.models import (
    ArticleImageJob,
    PartnerProduct,
    PartnerProductPromotion,
    Source,
    Topic,
)
from test_runtime_automation_events_unit import topic
from test_runtime_image_commands_unit import article, asset
from test_source_readiness_unit import NOW, source

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
