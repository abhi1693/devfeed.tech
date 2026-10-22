"""Image commands and real logo storage retain current identity and checkpoint ownership.

Repository reads are supplied rows. Storage and network clients are external
boundaries; database filtering, locks and atomic publication are not simulated.
"""

import copy
import re
import uuid
from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_aggregator import (
    image_storage_base,
    image_storage_tasks,
    logo_storage,
    product_logo_storage,
)
from devfeed_core import image_jobs, logos, source_lifecycle
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.job_lifecycle import start_job
from devfeed_core.models import ArticleImageJob, PartnerProduct, Source, Topic
from devfeed_core.object_storage import R2Storage
from devfeed_core.services import RecordNotFound
from test_runtime_article_worker_unit import article
from test_runtime_automation_events_unit import topic
from test_runtime_image_admission_unit import product
from test_runtime_image_worker_routing_unit import running
from test_source_readiness_unit import NOW, configuration, source
from test_topic_logo_storage import picture


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
    current = article()
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
