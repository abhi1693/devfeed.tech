"""Private image admission and receipts never replace a live approved asset early."""

import uuid
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import image_storage_tasks as storage
from devfeed_aggregator import image_tasks as discovery
from devfeed_core import article_jobs, article_refresh, image_jobs, managed_images
from devfeed_core.images import PageImage
from devfeed_core.models import ArticleImageJob
from devfeed_core.services import OperationConflict, RecordNotFound
from sqlalchemy.orm import Session

from tests.support import article_maintenance_unit as support
from tests.support import source_readiness
from tests.support.article_maintenance_unit import prepared as prepared


@pytest.fixture
def images(prepared, monkeypatch):
    public, owner, _, _ = prepared
    public.image_url = "https://publisher.example/approved.png"
    public.managed_image = {"public_receipt": "preserve"}
    owner.target, owner.image_url, owner.managed_image = "images", None, {}
    job = ArticleImageJob(
        id=uuid.uuid4(),
        article_id=public.id,
        refresh_id=owner.id,
        operation="discover",
        status="queued",
        attempts=0,
        storage={},
        available_at=support.NOW - timedelta(seconds=1),
    )
    owner.discovery_job_id = job.id
    read, session = Mock(spec=Session), Mock(spec=Session)
    session.info = {}
    factory = support.WorkerSessions(read, session)
    settings = source_readiness.configuration(
        image_storage_enabled=True, image_public_url="https://images.example"
    )
    value = SimpleNamespace(
        public=public,
        owner=owner,
        job=job,
        read=read,
        session=session,
        factory=factory,
        settings=settings,
        created=[],
    )

    def add(row):
        value.created.append(row)

    def flush():
        for row in value.created:
            if row.id is None:
                row.id = uuid.uuid4()

    session.add.side_effect = add
    session.flush.side_effect = flush
    value.authority = Mock(return_value=[public.origins[0].source])
    value.current = Mock(return_value=owner)
    monkeypatch.setattr(article_jobs, "approved_sources", value.authority)
    monkeypatch.setattr(article_refresh, "refresh_for_article", value.current)
    for module in (image_jobs, managed_images, discovery):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    for module in (image_jobs, discovery, storage):
        monkeypatch.setattr(module, "utcnow", lambda: support.NOW)
    return value


@pytest.mark.parametrize(
    "change", ["unbound", "operation", "owner", "slot", "article", "store_url"]
)
def test_only_exact_selected_private_image_work_can_use_a_candidate(images, change):
    r = images
    if change == "unbound":
        r.job.refresh_id = None
    elif change == "operation":
        r.job.operation = "source-logo"
    elif change == "owner":
        r.current.return_value = None
    elif change == "slot":
        r.owner.discovery_job_id = uuid.uuid4()
    elif change == "article":
        r.job.article_id = uuid.uuid4()
    else:
        r.job.operation, r.job.image_url = "store", "https://publisher.example/other.png"
        r.owner.storage_job_id = r.job.id
    assert image_jobs.current_refresh_image(r.session, r.public, r.job) is None
    assert r.public.image_url.endswith("approved.png")


@pytest.mark.parametrize("store", [False, True])
def test_selected_private_image_operation_has_an_explicit_owner_slot(images, store):
    r = images
    if store:
        r.job.operation = "store"
        r.job.image_url = r.owner.image_url = "https://publisher.example/private.png"
        r.owner.storage_job_id = r.job.id
    assert image_jobs.current_refresh_image(r.session, r.public, r.job) is r.owner
    assert image_jobs.refresh_image_slot(r.job) == ("storage" if store else "discovery")


@pytest.mark.parametrize(
    "variant",
    [
        "discover",
        "store",
        "forced",
        "active",
        "foreign",
        "unselected",
        "dedup",
        "missing",
        "stale",
        "already_present",
    ],
)
def test_private_admission_keeps_live_asset_and_binds_only_new_selected_work(
    images, monkeypatch, variant
):
    r = images
    if variant in {"store", "already_present"}:
        r.owner.image_url = "https://publisher.example/private.png"
    if variant == "already_present":
        monkeypatch.setattr(image_jobs, "image_variants", Mock(return_value=[{"width": 320}]))
    if variant == "stale":
        r.current.return_value = None
    active = r.job if variant in {"active", "foreign", "unselected"} else None
    if variant == "foreign":
        r.job.refresh_id = uuid.uuid4()
    elif variant == "unselected":
        r.owner.discovery_job_id = uuid.uuid4()
    r.session.scalar.side_effect = [
        None if variant == "missing" else r.public,
        active,
        uuid.uuid4() if variant == "dedup" else None,
    ]
    before = (r.public.image_url, deepcopy(r.public.managed_image))

    def request():
        return image_jobs.request_image(
            r.session,
            r.public.id,
            refresh_id=r.owner.id,
            refresh=variant == "forced",
            automatic=variant == "dedup",
        )

    if variant == "missing":
        with pytest.raises(RecordNotFound):
            request()
    elif variant in {"stale", "foreign", "unselected"}:
        with pytest.raises(OperationConflict):
            request()
    else:
        result = request()
        if variant in {"dedup", "already_present"}:
            assert result is None and not r.created
        elif variant == "active":
            assert result is r.job and not r.created
        else:
            assert result is r.created[0] and result.refresh_id == r.owner.id
            assert getattr(r.owner, f"{image_jobs.refresh_image_slot(result)}_job_id") == result.id
            assert result.operation == ("store" if variant == "store" else "discover")
            if variant == "forced":
                assert result.storage["article_refresh"] is True
    assert (r.public.image_url, r.public.managed_image) == before


@pytest.mark.parametrize(
    "variant",
    [
        "store",
        "discover",
        "closed",
        "unselected",
        "missing_article",
        "foreign_active",
        "changed_image",
        "checkpoint_version",
        "not_failed",
    ],
)
def test_private_image_retry_retains_only_matching_owner_and_checkpoint_inputs(images, variant):
    r = images
    previous = r.job
    previous.status = "failed"
    if variant != "discover":
        previous.operation = "store"
        previous.image_url = r.owner.image_url = "https://publisher.example/private.png"
        r.owner.storage_job_id = previous.id
        previous.storage = {**storage_asset(previous.image_url), "article_refresh": True}
    else:
        previous.storage = {"input_url": r.public.canonical_url, "private_old_lookup": True}
    if variant == "closed":
        r.owner.status = "cancelled"
    elif variant == "unselected":
        r.owner.storage_job_id = uuid.uuid4()
    elif variant == "changed_image":
        r.owner.image_url = "https://publisher.example/changed.png"
    elif variant == "checkpoint_version":
        previous.storage_version = "previous-derivative-format"
    elif variant == "not_failed":
        previous.status = "queued"
    active = (
        ArticleImageJob(
            id=uuid.uuid4(),
            article_id=r.public.id,
            refresh_id=uuid.uuid4(),
            status="queued",
            operation="store",
        )
        if variant == "foreign_active"
        else None
    )
    r.session.get.return_value = previous
    r.session.scalar.side_effect = [
        None if variant == "missing_article" else r.public,
        r.public,
        active,
    ]
    before = r.public.image_url, deepcopy(r.public.managed_image), deepcopy(previous.storage)
    blocked = variant in {
        "closed",
        "unselected",
        "missing_article",
        "foreign_active",
        "changed_image",
        "not_failed",
    }
    if blocked:
        with pytest.raises(OperationConflict):
            image_jobs.retry_image(r.session, previous.id)
        assert not r.created
    else:
        job = image_jobs.retry_image(r.session, previous.id)
        assert job is r.created[0] and job.refresh_id == previous.refresh_id == r.owner.id
        assert job.id != previous.id
        assert getattr(r.owner, f"{image_jobs.refresh_image_slot(job)}_job_id") == job.id
        if variant == "store":
            assert job.storage == previous.storage
            assert job.storage is not previous.storage
        else:
            assert "original_key" not in job.storage and "private_old_lookup" not in job.storage
    assert (r.public.image_url, r.public.managed_image, previous.storage) == before


@pytest.mark.parametrize(
    "variant",
    ["store", "discover", "already_present", "stale", "preview_changed", "missing_preview"],
)
def test_private_image_claims_preserve_publication_and_recheck_selected_subject(
    images, monkeypatch, variant
):
    r = images
    if variant == "store":
        r.job.operation = "store"
        r.job.image_url = r.owner.image_url = "https://publisher.example/private.png"
        r.owner.storage_job_id = r.job.id
    elif variant == "already_present":
        r.owner.image_url = "https://publisher.example/private.png"
    elif variant == "stale":
        r.current.return_value = None
    preview = SimpleNamespace(article_id=r.public.id, refresh_id=r.owner.id)
    if variant == "preview_changed":
        preview.refresh_id = uuid.uuid4()
    r.session.scalar.side_effect = (
        [None if variant == "missing_preview" else preview] + [None] * 5 + [r.job]
    )
    r.session.get.return_value = r.public
    request = Mock()
    monkeypatch.setattr(image_jobs, "request_image", request)
    result = image_jobs.claim_image(r.session, r.job.id)
    if variant in {"store", "discover"}:
        assert result == (r.job, r.job.image_url if variant == "store" else r.public.canonical_url)
        assert r.job.status == "running" and r.job.lease_token is not None
    else:
        assert result is None
        if variant == "already_present":
            request.assert_called_once_with(
                r.session, r.public.id, automatic=True, refresh_id=r.owner.id
            )
    assert r.public.publication_status == "published"
    assert r.public.image_url.endswith("approved.png")


@pytest.mark.parametrize(
    "variant", ["found", "missing", "changed_url", "stale", "newer_image", "no_storage"]
)
def test_discovery_settles_only_private_receipts_and_queues_owned_storage(
    images, monkeypatch, variant
):
    r = images
    image = (
        None
        if variant == "missing"
        else PageImage("https://publisher.example/new.png", "opengraph")
    )
    if variant == "stale":
        r.current.return_value = None
    elif variant == "newer_image":
        r.owner.image_url = "https://publisher.example/newer.png"
    elif variant == "no_storage":
        r.settings.image_storage_enabled = False
    request = Mock()
    monkeypatch.setattr(discovery, "request_image", request)
    url = "https://other.example/" if variant == "changed_url" else r.public.canonical_url
    result = discovery._finish_refresh_discovery(r.session, r.public, r.job, url, image)
    if variant in {"stale", "changed_url"}:
        assert result is None and r.job.outcome == "already_present"
        request.assert_not_called()
    else:
        assert result == r.owner.id and r.owner.version == 2
        assert r.job.outcome == (
            "not_found"
            if variant == "missing"
            else "already_present"
            if variant == "newer_image"
            else "found"
        )
        if variant == "newer_image":
            assert r.owner.image_url.endswith("newer.png")
        elif variant != "missing":
            assert r.owner.image_url == image.url
        assert request.call_count == (0 if variant == "no_storage" else 1)
    assert r.public.image_url.endswith("approved.png") and r.public.managed_image == {
        "public_receipt": "preserve"
    }


def storage_asset(url):
    return {
        "source_url": url,
        "version": "v1",
        "hash": "a" * 64,
        "original_key": "originals/a.png",
        "source_width": 960,
        "variants": [{"key": "thumbnails/a.webp", "width": 320}],
    }


@pytest.mark.parametrize("variant", ["current", "lease", "foreign", "article", "owner", "url"])
def test_storage_checkpoint_has_source_subject_lease_and_private_owner_authority(
    images, monkeypatch, variant
):
    r = images
    r.job.operation = "store"
    r.job.image_url = r.owner.image_url = "https://publisher.example/private.png"
    r.owner.storage_job_id = r.job.id
    monkeypatch.setattr(
        storage, "_lock_subject", lambda *_: None if variant == "article" else r.public
    )
    lease = Mock(return_value=None if variant == "lease" else r.job)
    monkeypatch.setattr(storage, "owned_job", lease)
    if variant == "foreign":
        r.job.refresh_id = uuid.uuid4()
    elif variant == "owner":
        r.current.return_value = None
    elif variant == "url":
        r.owner.image_url = "https://publisher.example/newer.png"
    r.job.storage = {"article_refresh": True, "source_pipeline_request": "marker"}
    current = storage._refresh_storage_owner(
        r.session, r.public.id, r.job.id, uuid.uuid4(), r.owner.id
    )
    assert (current is not None) == (variant == "current")
    saved = storage.checkpoint(
        r.factory,
        r.job.id,
        uuid.uuid4(),
        storage_asset(r.job.image_url),
        refresh_id=r.owner.id,
        article_id=r.public.id,
    )
    assert saved == (variant == "current")
    if saved:
        assert r.job.storage["source_pipeline_request"] == "marker"
        assert r.job.storage["article_refresh"] is True
        assert r.owner.managed_image == {}
    assert r.public.image_url.endswith("approved.png")


@pytest.mark.parametrize("variant", ["current", "lost", "source_changed"])
def test_complete_storage_publishes_only_a_private_manifest_before_postcommit_promotion(
    images, monkeypatch, variant
):
    r = images
    r.owner.image_url = "https://publisher.example/private.png"
    r.job.image_url, r.job.operation = r.owner.image_url, "store"
    asset = storage_asset(r.owner.image_url)
    monkeypatch.setattr(
        storage,
        "_refresh_storage_owner",
        lambda *_: None if variant == "lost" else (r.public, r.job, r.owner),
    )

    def after_commit(factory, refresh_id):
        assert factory is r.factory and refresh_id == r.owner.id and not r.factory.writing
        assert r.job.status == "succeeded" and r.owner.managed_image == asset
        return "promoted"

    promoted = Mock(side_effect=after_commit)
    monkeypatch.setattr(article_refresh, "try_promote_refresh", promoted)
    source = (
        "https://publisher.example/stale.png" if variant == "source_changed" else r.owner.image_url
    )
    storage._finish_refresh_storage(
        r.factory, r.job.id, uuid.uuid4(), r.public.id, r.owner.id, source, asset, "opengraph"
    )
    if variant == "current":
        assert r.owner.managed_image == asset and r.job.outcome == "found"
        promoted.assert_called_once_with(r.factory, r.owner.id)
    else:
        promoted.assert_not_called()
    assert r.public.image_url.endswith("approved.png")
