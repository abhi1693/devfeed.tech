"""Background commands expose durable work and dispatch after database scopes close."""

import uuid
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_cli import articles, commands, images
from devfeed_core.managed_images import ARTICLE_THUMBNAIL_LAYOUT, THUMBNAIL_VERSION
from devfeed_core.models import ArticleEnrichmentJob, ArticleImageJob
from devfeed_core.services import RecordNotFound

from tests.support.source_readiness import job

URL = "https://publisher.example/routing"


def queued(kind, identifier):
    if kind == "article-enrichment":
        return job(
            ArticleEnrichmentJob,
            article_id=identifier,
            result={"input_url": URL},
            changed_fields=[],
            dispatched_at=None,
            http_status=None,
            outcome=None,
        )
    return job(
        ArticleImageJob,
        article_id=identifier,
        operation="discover",
        storage={"input_url": URL},
        image_url=None,
        method=None,
        dispatched_at=None,
        http_status=None,
        outcome=None,
    )


@pytest.fixture
def store(monkeypatch):
    session, phases = Mock(), []

    @contextmanager
    def factory():
        phases.append("open")
        try:
            yield session
        finally:
            phases.append("closed")

    factory.begin = factory
    for module in (articles, images):
        monkeypatch.setattr(module, "session_factory", lambda: factory)
    return NS(session=session, phases=phases)


@pytest.mark.parametrize("operation", ["enrich", "article-retry", "image-retry"])
@pytest.mark.parametrize("outcome", ["already-present", "queued", "force"])
def test_scheduling_and_retry_preserve_completed_metadata_or_publish_new_typed_work(
    monkeypatch, store, operation, outcome
):
    image = operation == "image-retry"
    module, handler, request_name, kind = (
        (images, images.retry, "retry_image", "images")
        if image
        else (
            articles,
            articles.enrich if operation == "enrich" else articles.retry,
            "request_article_enrichment" if operation == "enrich" else "retry_article",
            "article-enrichment",
        )
    )
    identifier = uuid.uuid4()
    pending = queued(kind, identifier)
    request = Mock(return_value=None if outcome == "already-present" else pending)
    monkeypatch.setattr(module, request_name, request)

    def dispatch(actual, **options):
        assert store.phases == ["open", "closed"]
        assert actual == pending.id and options == {"kind": kind}
        return {"id": str(actual), "published": True}

    publication = Mock(side_effect=dispatch)
    monkeypatch.setattr(module, "dispatch_now", publication)
    output = handler(NS(id=identifier, force=outcome == "force"))
    assert output["already_present" if image else "publisher_metadata_present"] is (
        outcome == "already-present"
    )
    request.assert_called_once_with(store.session, identifier)
    if outcome == "already-present":
        assert output["job"] is None
    else:
        assert output["job"]["id"] == str(pending.id)
        assert output["job"].get("published", False) is (outcome == "force")
        assert pending.status == "queued" and pending.lease_token is None
    assert publication.call_count == int(outcome == "force")


@pytest.mark.parametrize("dispatch", [False, True])
def test_article_backfill_reports_saved_count_and_dispatches_after_all_jobs_leave_command_scope(
    monkeypatch, store, dispatch
):
    source_id = uuid.uuid4()
    pending = [queued("article-enrichment", uuid.uuid4()) for _ in range(2)]
    schedule = Mock(return_value=pending)
    monkeypatch.setattr(articles, "backfill_articles", schedule)

    def publish(identifier, **options):
        assert store.phases == ["open", "closed"]
        assert options == {"kind": "article-enrichment"}
        return {"id": str(identifier), "published": True}

    publication = Mock(side_effect=publish)
    monkeypatch.setattr(articles, "dispatch_now", publication)
    output = articles.backfill(NS(limit=2, source_id=source_id, dispatch=dispatch))
    assert output["queued"] == 2 and [row["id"] for row in output["jobs"]] == [
        str(current.id) for current in pending
    ]
    schedule.assert_called_once_with(store.session, 2, source_id=source_id)
    assert publication.call_count == (2 if dispatch else 0)


@pytest.mark.parametrize("module,kind", [(articles, "article-enrichment"), (images, "images")])
def test_job_page_and_detail_use_typed_run_identity_and_never_serialize_private_leases(
    store, module, kind
):
    current = queued(kind, uuid.uuid4())
    store.session.scalars.return_value = [current]
    store.session.get.return_value = current
    page = module.jobs(NS(status="queued", article_id=current.article_id, offset=3, limit=1))
    detail = module.show(NS(id=current.id))
    assert [item["id"] for item in page] == [detail["id"]] == [str(current.id)]
    assert detail["article_id"] == str(current.article_id)
    assert "lease_token" not in detail and "lease_until" not in detail


@pytest.mark.parametrize("module", [articles, images])
def test_missing_job_detail_is_a_not_found_error_instead_of_a_null_success(store, module):
    store.session.get.return_value = None
    with pytest.raises(RecordNotFound, match="job not found"):
        module.show(NS(id=uuid.uuid4()))


@pytest.mark.parametrize("operation", ["recompress", "resize"])
def test_thumbnail_maintenance_returns_only_the_scheduled_typed_jobs(monkeypatch, store, operation):
    current = queued("images", uuid.uuid4())
    current.operation, current.image_url = "store", "https://publisher.example/hero.png"
    current.storage_version = THUMBNAIL_VERSION
    current.storage = {
        "source_url": current.image_url,
        "version": "v1",
        "thumbnail_version": THUMBNAIL_VERSION,
        "hash": "a" * 64,
        "original_key": "originals/saved-hero",
        "source_width": 1600,
        "variants": []
        if operation == "recompress"
        else [
            {"key": f"thumbnails/v2/saved-hero/{width}.webp", "width": width}
            for width in (320, 640, 960)
        ],
        **({"thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT} if operation == "resize" else {}),
    }
    schedule = Mock(return_value=[current])
    monkeypatch.setattr(
        images,
        "backfill_thumbnail_encoding" if operation == "recompress" else "backfill_thumbnail_sizes",
        schedule,
    )
    output = getattr(images, operation)(NS(limit=7))
    assert output["queued"] == 1 and output["jobs"][0]["id"] == str(current.id)
    assert output["jobs"][0]["operation"] == "store"
    assert output["jobs"][0]["storage"]["original_key"] == "originals/saved-hero"
    schedule.assert_called_once_with(store.session, 7)


@pytest.mark.parametrize(
    "subject,present", [("topic", False), ("topic", True), ("source", False), ("source", True)]
)
def test_logo_submission_preserves_subject_and_explicit_source_refresh(
    monkeypatch, store, subject, present
):
    identifier = uuid.uuid4()
    current = job(
        ArticleImageJob,
        article_id=None,
        topic_id=identifier if subject == "topic" else None,
        source_id=identifier if subject == "source" else None,
        operation="topic-logo" if subject == "topic" else "source-logo-refresh",
        image_url="https://publisher.example/icon.png",
        storage={},
        dispatched_at=None,
        http_status=None,
        outcome=None,
        method=None,
    )
    request = Mock(return_value=current if present else None)
    monkeypatch.setattr(
        images, "request_topic_logo" if subject == "topic" else "request_source_logo", request
    )
    output = (images.topic_logo if subject == "topic" else images.source_logo)(
        NS(id=identifier, refresh=True)
    )
    request.assert_called_once_with(
        store.session, identifier, **({"refresh": True} if subject == "source" else {})
    )
    assert (output is None) is (not present)
    if present:
        assert output[f"{subject}_id"] == str(identifier) and output["article_id"] is None


@pytest.mark.parametrize("operation", ["detect_languages", "restore_tags"])
def test_metadata_maintenance_preserves_cursor_source_and_dry_run(monkeypatch, operation):
    after, source = uuid.uuid4(), uuid.uuid4()
    command = Mock(return_value={"scanned": 9, "updated": 3, "dry_run": True})
    monkeypatch.setattr(
        articles,
        "backfill_languages" if operation == "detect_languages" else "backfill_tags",
        command,
    )
    output = getattr(articles, operation)(NS(limit=9, after=after, source_id=source, dry_run=True))
    command.assert_called_once_with(limit=9, after=after, source_id=source, dry_run=True)
    assert output == {"scanned": 9, "updated": 3, "dry_run": True}


@pytest.mark.parametrize(
    "operation,kind",
    [
        (articles.dispatch, "article-enrichment"),
        (images.dispatch, "images"),
        (commands.source_enrichment_dispatch, "source-enrichment"),
        (commands.job_dispatch, None),
    ],
)
def test_explicit_dispatch_uses_the_selected_pipeline_transport(monkeypatch, operation, kind):
    identifier, dispatch = uuid.uuid4(), Mock(return_value={"dispatched": True})
    if operation in {commands.source_enrichment_dispatch, commands.job_dispatch}:
        monkeypatch.setattr(commands.dispatch, "dispatch_now", dispatch)
    else:
        monkeypatch.setattr(
            articles if kind == "article-enrichment" else images, "dispatch_now", dispatch
        )
    assert operation(NS(id=identifier)) == {"dispatched": True}
    dispatch.assert_called_once_with(identifier, **({"kind": kind} if kind else {}))
