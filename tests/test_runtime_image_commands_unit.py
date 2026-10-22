"""Image admission, lease and checkpoint policy over repository rows.

These do not prove row locks, concurrent coalescing or database query eligibility.
"""

import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import image_jobs as images
from devfeed_core import logos, managed_images
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.jobs import LEASE_SECONDS
from devfeed_core.managed_images import ARTICLE_THUMBNAIL_LAYOUT, THUMBNAIL_VERSION
from devfeed_core.models import Article, ArticleImageJob, Source
from devfeed_core.services import OperationConflict
from test_processing_retry_eligibility_unit import scenario
from test_source_readiness_unit import NOW, configuration, source


@pytest.fixture
def settings(monkeypatch):
    value = configuration(image_storage_enabled=True, image_public_url="https://images.example")
    for module in (images, managed_images, logos):
        monkeypatch.setattr(module, "get_settings", lambda: value)
    monkeypatch.setattr(images, "utcnow", lambda: NOW)
    return value


def article(**changes):
    return Article(
        **{
            "id": uuid.uuid4(),
            "canonical_url": "https://publisher.example/article",
            "title": "Image storage",
            "review_status": "pending",
            "publication_status": "unpublished",
            "image_url": "https://publisher.example/preview.png",
            "managed_image": {},
            **changes,
        }
    )


def asset(url, *, version=THUMBNAIL_VERSION, digest="a" * 64):
    return {
        "source_url": url,
        "version": "v1",
        "thumbnail_version": version,
        "hash": digest,
        "original_key": f"originals/{digest}.png",
        "source_width": 960,
        "variants": [{"key": f"thumbnails/{version}/{digest}/960.webp", "width": 960}],
    }


def job(current, **changes):
    return ArticleImageJob(
        **{
            "id": uuid.uuid4(),
            "article_id": current.id if isinstance(current, Article) else None,
            "source_id": current.id if isinstance(current, Source) else None,
            "operation": "store" if isinstance(current, Article) else "source-logo",
            "image_url": current.image_url if isinstance(current, Article) else current.logo_url,
            "storage": {},
            "storage_version": None if isinstance(current, Article) else logos.LOGO_VERSION,
            "status": "queued",
            "attempts": 0,
            "created_at": NOW - timedelta(minutes=2),
            "available_at": NOW - timedelta(minutes=1),
            "lease_token": None,
            "lease_until": None,
            **changes,
        }
    )


def claim_session(current, queued):
    session = Mock()
    # Subject locks are database operations; only the final admitted job is supplied.
    session.scalar.side_effect = [None] * 5 + [queued]
    session.get.return_value = current
    return session


@pytest.mark.parametrize("automatic", [False, True])
def test_published_article_requires_unpublishing_before_new_image_work(settings, automatic):
    from unittest.mock import patch

    with patch("test_article_lifecycle.utcnow", lambda: NOW):
        current, facts = scenario()
    assert derive_publication_readiness(current, **facts).stage == "ready"
    current.publication_status = "published"
    current.published_to_feed_at = facts["now"]
    session = Mock()
    session.scalar.return_value = current
    if automatic:
        assert images.request_image(session, current.id, automatic=True) is None
    else:
        with pytest.raises(OperationConflict, match="Unpublish"):
            images.request_image(session, current.id, refresh=True)
    session.add.assert_not_called()
    assert current.publication_status == "published"


@pytest.mark.parametrize("change", ["disabled", "url", "stored", "current"])
def test_storage_claim_rechecks_current_input_and_only_leases_work_that_is_still_needed(
    settings, monkeypatch, change
):
    current = article()
    queued = job(current)
    if change == "disabled":
        settings.image_storage_enabled = False
    elif change == "url":
        current.image_url = "https://publisher.example/replaced.png"
    elif change == "stored":
        current.managed_image = asset(current.image_url)
    request = Mock()
    monkeypatch.setattr(images, "request_image", request)
    session = claim_session(current, queued)
    result = images.claim_image(session, queued.id)
    if change == "current":
        assert result == (queued, current.image_url) and queued.status == "running"
        assert queued.attempts == 1 and queued.lease_token is not None
        assert queued.lease_until == NOW + timedelta(seconds=LEASE_SECONDS)
        request.assert_not_called()
    else:
        assert result is None and queued.attempts == 0 and queued.lease_token is None
        if change == "disabled":
            assert queued.status == "queued"
            request.assert_not_called()
        else:
            assert queued.status == "succeeded" and queued.outcome == "already_present"
            request.assert_called_once_with(session, current.id, automatic=True)


@pytest.mark.parametrize("storage", [False, True])
def test_publisher_image_arriving_before_discovery_is_not_replaced_by_the_old_lookup(
    settings, monkeypatch, storage
):
    current = article(image_url=None)
    queued = job(
        current, operation="discover", image_url=None, storage={"input_url": current.canonical_url}
    )
    current.image_url = "https://publisher.example/feed-preview.png"
    settings.image_storage_enabled = storage
    request = Mock()
    monkeypatch.setattr(images, "request_image", request)
    assert images.claim_image(claim_session(current, queued), queued.id) is None
    assert (
        queued.status == "succeeded"
        and queued.outcome == "already_present"
        and queued.attempts == 0
    )
    assert current.image_url.endswith("feed-preview.png")
    assert request.call_count == int(storage)


@pytest.mark.parametrize("change", ["disabled", "url", "version", "stored", "current", "refresh"])
def test_source_logo_claim_retires_stale_work_but_an_explicit_refresh_can_replace_a_current_logo(
    settings, monkeypatch, change
):
    current = source(logo_url="https://publisher.example/logo.png")
    queued = job(current)
    if change == "disabled":
        settings.image_storage_enabled = False
    elif change == "url":
        current.logo_url = "https://publisher.example/replacement.png"
    elif change == "version":
        queued.storage_version = "old-logo-format"
    if change in {"stored", "refresh"}:
        current.managed_logo = {
            "source_url": current.logo_url,
            "version": logos.LOGO_VERSION,
            "variants": [
                {"key": f"logos/current/{width}.webp", "width": width}
                for width in logos.logo_sizes(Source)
            ],
        }
    if change == "refresh":
        queued.operation = "source-logo-refresh"
    request = Mock()
    monkeypatch.setattr(logos, "request_logo", request)
    session = claim_session(current, queued)
    result = images.claim_image(session, queued.id)
    if change in {"current", "refresh"}:
        assert result == (queued, current.logo_url) and queued.status == "running"
        assert queued.attempts == 1 and queued.lease_until == NOW + timedelta(seconds=LEASE_SECONDS)
        request.assert_not_called()
    else:
        assert result is None and queued.attempts == 0
        if change == "disabled":
            assert queued.status == "queued"
            request.assert_not_called()
        else:
            assert queued.status == "succeeded" and queued.outcome == "already_present"
            request.assert_called_once_with(session, Source, current.id, automatic=True)


@pytest.mark.parametrize("operation", ["recompress", "resize"])
@pytest.mark.parametrize("active", [False, True])
def test_thumbnail_maintenance_preserves_the_served_asset_and_reuses_active_work(
    settings, operation, active
):
    current = article()
    current.managed_image = asset(
        current.image_url, version="v1" if operation == "recompress" else THUMBNAIL_VERSION
    )
    before = copy.deepcopy(current.managed_image)
    running = job(
        current,
        storage_version=THUMBNAIL_VERSION,
        storage={
            **before,
            **(
                {"thumbnail_version": THUMBNAIL_VERSION, "variants": []}
                if operation == "recompress"
                else {"thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT}
            ),
        },
        status="running",
        attempts=1,
        lease_token=uuid.uuid4(),
        lease_until=NOW + timedelta(minutes=4),
    )
    session = Mock()
    session.scalar.side_effect = [current, running if active else None]
    request = (
        images.request_thumbnail_recompression
        if operation == "recompress"
        else images.request_thumbnail_resize
    )
    chosen = request(session, current.id)
    assert current.managed_image == before
    if active:
        assert chosen is running and chosen.status == "running" and chosen.attempts == 1
        session.add.assert_not_called()
    else:
        assert chosen.article_id == current.id and chosen.image_url == current.image_url
        assert chosen.operation == "store" and chosen.storage_version == THUMBNAIL_VERSION
        assert chosen.storage["original_key"] == before["original_key"]
        if operation == "recompress":
            assert (
                chosen.storage["variants"] == []
                and chosen.storage["thumbnail_version"] == THUMBNAIL_VERSION
            )
        else:
            assert chosen.storage["thumbnail_layout"] == ARTICLE_THUMBNAIL_LAYOUT
        session.add.assert_called_once_with(chosen)


@pytest.mark.parametrize("change", ["current", "url", "hash"])
def test_resize_reuses_only_a_checkpoint_from_the_same_original(settings, change):
    current = article()
    current.managed_image = asset(current.image_url)
    checkpoint = {
        **current.managed_image,
        "thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT,
        "variants": current.managed_image["variants"]
        + [{"key": "thumbnails/v2/partial/480.webp", "width": 480}],
    }
    if change == "url":
        checkpoint["source_url"] = "https://publisher.example/old.png"
    elif change == "hash":
        checkpoint["hash"] = "b" * 64
    session = Mock()
    session.scalar.side_effect = [current, None]
    chosen = images.request_thumbnail_resize(session, current.id, checkpoint=checkpoint)
    assert chosen.storage["source_url"] == current.image_url
    assert chosen.storage["hash"] == current.managed_image["hash"]
    assert len(chosen.storage["variants"]) == (2 if change == "current" else 1)
    assert chosen.storage["thumbnail_layout"] == ARTICLE_THUMBNAIL_LAYOUT


@pytest.mark.parametrize("operation", ["recompress", "resize"])
def test_thumbnail_maintenance_is_not_scheduled_without_a_complete_saved_original(
    settings, operation
):
    current = article()
    current.managed_image = asset(
        current.image_url, version="v1" if operation == "recompress" else THUMBNAIL_VERSION
    )
    current.managed_image.pop("original_key")
    session = Mock()
    session.scalar.return_value = current
    request = (
        images.request_thumbnail_recompression
        if operation == "recompress"
        else images.request_thumbnail_resize
    )
    assert request(session, current.id) is None
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "operation", ["source-logo", "source-logo-refresh", "resize", "recompress"]
)
def test_retry_routes_to_the_owning_maintenance_command_without_changing_failed_history(
    settings, monkeypatch, operation
):
    current = (
        source(logo_url="https://publisher.example/logo.png")
        if operation.startswith("source-")
        else article()
    )
    storage = (
        {
            "source_url": current.logo_url,
            "version": logos.LOGO_VERSION,
            "hash": "a" * 64,
            "original_key": "logos/original.png",
            "variants": [{"key": "logos/16.webp", "width": 16}],
        }
        if isinstance(current, Source)
        else asset(current.image_url)
    )
    if operation == "resize":
        storage["thumbnail_layout"] = ARTICLE_THUMBNAIL_LAYOUT
    previous = job(
        current,
        operation=operation if operation.startswith("source-") else "store",
        storage=storage,
        storage_version=THUMBNAIL_VERSION
        if operation in {"resize", "recompress"}
        else logos.LOGO_VERSION,
        status="failed",
        attempts=3,
        error="Object store unavailable",
        finished_at=NOW,
    )
    replacement = job(
        current,
        operation=previous.operation,
        storage_version=previous.storage_version,
        created_at=NOW,
        available_at=NOW,
    )
    session, boundary = Mock(), Mock(return_value=replacement)
    session.get.return_value = previous
    if operation.startswith("source-"):
        monkeypatch.setattr(logos, "request_logo", boundary)
    else:
        monkeypatch.setattr(
            images,
            "request_thumbnail_resize"
            if operation == "resize"
            else "request_thumbnail_recompression",
            boundary,
        )
    chosen = images.retry_image(session, previous.id)
    assert (
        chosen is replacement
        and previous.status == "failed"
        and previous.error == "Object store unavailable"
    )
    if operation.startswith("source-"):
        boundary.assert_called_once_with(
            session, Source, current.id, refresh=operation == "source-logo-refresh"
        )
    elif operation == "resize":
        boundary.assert_called_once_with(session, current.id, checkpoint=previous.storage)
    else:
        boundary.assert_called_once_with(session, current.id)
    assert chosen.storage == previous.storage
