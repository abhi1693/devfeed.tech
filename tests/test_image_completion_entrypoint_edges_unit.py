"""Image entry points retain subject type, safe input failure, and durable completion handoff.

Preselected repository results include the confirmed conditional article update.
The tests do not evaluate its SQL predicates or claim transaction atomicity.
"""

import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_aggregator import (
    image_tasks,
    logo_storage,
    product_image_storage,
    product_logo_storage,
)
from devfeed_core import image_jobs, logos, managed_images, partner_product_images, topic_logos
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.models import ArticleImageJob, Topic
from devfeed_core.object_storage import R2Storage
from test_runtime_article_worker_unit import article
from test_runtime_automation_events_unit import topic
from test_runtime_image_admission_unit import product
from test_runtime_image_worker_routing_unit import Factory, running
from test_source_readiness_unit import NOW, configuration


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
    current = article()
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
