import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator import image_storage_tasks, image_tasks, logo_storage
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.image_jobs import retry_image
from devfeed_core.models import ArticleImageJob, Source, utcnow
from devfeed_core.schemas import SourceOut, SourcePublicOut
from devfeed_core.source_logos import backfill_source_logos, request_source_logo
from devfeed_core.topic_logos import SOURCE_LOGO_SIZES, logo_url
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.fixture
def enabled(database, monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.test")
    get_settings.cache_clear()
    monkeypatch.setattr(
        image_storage_tasks, "storage_client", lambda: SimpleNamespace(close=lambda: None)
    )
    monkeypatch.setattr(
        logo_storage,
        "store_logo_original",
        lambda client, url: {
            "source_url": url,
            "hash": "hash",
            "original_key": "originals/hash.png",
            "version": "v1",
            "variants": [],
        },
    )
    monkeypatch.setattr(
        logo_storage,
        "store_logo_variant",
        lambda client, asset, size: {"key": f"topic-logos/v1/hash/{size}.webp", "width": size},
    )
    return database


def source(factory):
    identifier = uuid.uuid4()
    with factory.begin() as session:
        session.add(
            Source(
                id=identifier,
                name="Logo",
                slug=f"logo-{identifier}",
                source_type="publisher",
                feed_url=f"https://remote.test/{identifier}.rss",
                approval_status="approved",
                logo_url="https://remote.test/logo.svg",
            )
        )
    with factory() as session:
        job_id = session.scalar(
            select(ArticleImageJob.id).where(ArticleImageJob.source_id == identifier)
        )
    return identifier, job_id


def process_logo(factory, job_id):
    image_tasks.enrich_image(str(job_id))
    with factory() as session:
        job = session.get(ArticleImageJob, job_id)
        resume = job.status == "queued" and job.storage and not job.error
    if resume:
        image_tasks.enrich_image(str(job_id))


def test_automatic_outbox_and_public_admin_contracts(enabled):
    identifier, job_id = source(enabled)
    assert job_id
    with enabled.begin() as session:
        assert request_source_logo(session, identifier).id == job_id
        assert backfill_source_logos(session) == []
        assert (
            SourcePublicOut.model_validate(session.get(Source, identifier)).logo_url
            == "https://remote.test/logo.svg"
        )
    process_logo(enabled, job_id)
    with enabled() as session:
        saved = session.get(Source, identifier)
        assert saved.logo_url == "https://remote.test/logo.svg"
        output = SourcePublicOut.model_validate(saved)
        assert output.logo_url == "https://images.test/topic-logos/v1/hash/64.webp"
        assert [v.width for v in output.logo_variants] == list(SOURCE_LOGO_SIZES)
        assert SourceOut.model_validate(saved).logo_url == saved.logo_url
        assert session.get(ArticleImageJob, job_id).status == "succeeded"


def test_retry_reuses_checkpoint_and_only_publishes_complete_asset(enabled, monkeypatch):
    identifier, job_id = source(enabled)
    calls = []

    def variant(client, asset, size):
        calls.append(size)
        if size == 64 and calls.count(64) == 1:
            raise FeedError("retry", reason="image_transform", retryable=True)
        return {"key": f"logo/{size}.webp", "width": size}

    monkeypatch.setattr(logo_storage, "store_logo_variant", variant)
    process_logo(enabled, job_id)
    with enabled.begin() as session:
        assert logo_url(session.get(Source, identifier)) == "https://images.test/originals/hash.png"
        job = session.get(ArticleImageJob, job_id)
        assert job.status == "queued"
        assert len(job.storage["variants"]) == 2
        job.available_at = utcnow() - timedelta(seconds=1)
    process_logo(enabled, job_id)
    assert calls == [16, 32, 64, 64, 96]
    with enabled() as session:
        assert logo_url(session.get(Source, identifier)).endswith("64.webp")


def test_source_changes_during_processing_schedule_replacement(enabled, monkeypatch):
    identifier, job_id = source(enabled)
    original = logo_storage.store_logo_original

    def change(client, url):
        with enabled.begin() as session:
            session.get(Source, identifier).logo_url = "https://remote.test/new.svg"
        return original(client, url)

    monkeypatch.setattr(logo_storage, "store_logo_original", change)
    process_logo(enabled, job_id)
    with enabled() as session:
        assert not session.get(Source, identifier).managed_logo
        assert session.get(ArticleImageJob, job_id).outcome == "already_present"
        replacement = session.scalar(
            select(ArticleImageJob).where(
                ArticleImageJob.source_id == identifier, ArticleImageJob.status == "queued"
            )
        )
        assert replacement.image_url.endswith("new.svg")


def test_manual_retry_retains_completed_variants(enabled, monkeypatch):
    identifier, job_id = source(enabled)

    def variant(client, asset, size):
        if size == 64:
            raise FeedError("bad", reason="image_transform", retryable=False)
        return {"key": f"logo/{size}.webp", "width": size}

    monkeypatch.setattr(logo_storage, "store_logo_variant", variant)
    process_logo(enabled, job_id)
    with enabled.begin() as session:
        old = session.get(ArticleImageJob, job_id)
        assert old.status == "failed"
        new = retry_image(session, job_id)
        assert new.id != old.id
        assert new.storage == old.storage
        assert len(new.storage["variants"]) == 2


def test_clear_and_replacement_failure_preserve_intent(enabled, monkeypatch):
    identifier, job_id = source(enabled)
    process_logo(enabled, job_id)
    with enabled.begin() as session:
        saved = session.get(Source, identifier)
        saved.logo_url = "https://remote.test/bad.svg"

    def fail(*args):
        raise FeedError("invalid", reason="invalid_topic_logo", retryable=False)

    monkeypatch.setattr(logo_storage, "store_logo_original", fail)
    with enabled() as session:
        replacement = session.scalar(
            select(ArticleImageJob.id).where(
                ArticleImageJob.source_id == identifier, ArticleImageJob.status == "queued"
            )
        )
    process_logo(enabled, replacement)
    with enabled.begin() as session:
        saved = session.get(Source, identifier)
        assert logo_url(saved).endswith("64.webp")
        saved.logo_url = None
        assert logo_url(saved) is None


def test_backfill_old_sources_is_bounded_and_idempotent(enabled, monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "false")
    get_settings.cache_clear()
    for _ in range(3):
        source(enabled)
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "true")
    get_settings.cache_clear()
    with enabled.begin() as session:
        assert len(backfill_source_logos(session, 2)) == 2
    with enabled.begin() as session:
        assert len(backfill_source_logos(session, 2)) == 1
    with enabled.begin() as session:
        assert backfill_source_logos(session, 2) == []


def test_backfill_skips_completed_assets_after_job_history_cleanup(enabled):
    from sqlalchemy import delete

    identifier, job_id = source(enabled)
    process_logo(enabled, job_id)
    with enabled.begin() as session:
        session.execute(delete(ArticleImageJob).where(ArticleImageJob.id == job_id))
        assert backfill_source_logos(session, 1) == []


def test_superseded_queued_job_schedules_current_source(enabled):
    identifier, job_id = source(enabled)
    with enabled.begin() as session:
        session.get(Source, identifier).logo_url = "https://remote.test/current.svg"
    process_logo(enabled, job_id)
    with enabled() as session:
        assert session.get(ArticleImageJob, job_id).outcome == "already_present"
        new = session.scalar(
            select(ArticleImageJob).where(
                ArticleImageJob.source_id == identifier, ArticleImageJob.status == "queued"
            )
        )
        assert new.image_url.endswith("current.svg")


def test_concurrent_source_edits_coalesce_outbox(enabled):
    from concurrent.futures import ThreadPoolExecutor

    identifier, job_id = source(enabled)
    process_logo(enabled, job_id)

    def edit(index):
        with enabled.begin() as session:
            saved = session.get(Source, identifier)
            saved.logo_url = f"https://remote.test/logo-{index}.svg"

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(edit, range(3)))
    with enabled() as session:
        active = session.scalars(
            select(ArticleImageJob).where(
                ArticleImageJob.source_id == identifier, ArticleImageJob.status == "queued"
            )
        ).all()
        assert len(active) == 1


def test_import_pass_saves_all_originals_before_variant_pass(enabled, monkeypatch):
    first, first_job = source(enabled)
    second, second_job = source(enabled)
    calls = []
    original = logo_storage.store_logo_original
    variant = logo_storage.store_logo_variant
    monkeypatch.setattr(
        logo_storage, "store_logo_original", lambda *a: (calls.append("original"), original(*a))[1]
    )
    monkeypatch.setattr(
        logo_storage, "store_logo_variant", lambda *a: (calls.append("variant"), variant(*a))[1]
    )
    for identifier in (first_job, second_job):
        image_tasks.enrich_image(str(identifier))
        with enabled() as session:
            job = session.get(ArticleImageJob, identifier)
            assert job.status == "queued" and job.attempts == 0
            assert not job.storage["variants"]
            assert logo_url(session.get(Source, job.source_id)).endswith("hash.png")
    assert calls == ["original", "original"]
    with enabled() as session:
        assert (
            session.get(ArticleImageJob, first_job).available_at
            > session.get(ArticleImageJob, second_job).created_at
        )
    for identifier in (first_job, second_job):
        image_tasks.enrich_image(str(identifier))
    assert calls == ["original", "original", *(["variant"] * 8)]


def test_admin_image_jobs_label_and_retry_source_logos(enabled):
    from devfeed_admin_api.jobs import jobs
    from devfeed_admin_api.pagination import ListQuery

    identifier, job_id = source(enabled)
    with enabled.begin() as session:
        session.get(ArticleImageJob, job_id).status = "failed"
    with enabled() as session:
        result = jobs(
            "images",
            session,
            ListQuery(q="", sort=None, limit=25, offset=0),
            source_id=identifier,
            status="failed",
        )
        assert result["total"] == 1
        assert result["items"][0].source_id == identifier
        assert result["items"][0].target_name == "Logo"
        assert result["items"][0].retryable
    with enabled.begin() as session:
        retry_image(session, job_id)
    with enabled() as session:
        result = jobs(
            "images",
            session,
            ListQuery(q="", sort=None, limit=25, offset=0),
            source_id=identifier,
            status="retried",
        )
        assert result["total"] == 1
        assert not result["items"][0].retryable


def test_explicit_refresh_imports_changed_artwork_at_same_url(enabled, monkeypatch):
    identifier, job_id = source(enabled)
    process_logo(enabled, job_id)
    with enabled.begin() as session:
        saved = session.get(Source, identifier)
        old = dict(saved.managed_logo)
        assert request_source_logo(session, identifier) is None
        refresh = request_source_logo(session, identifier, refresh=True)
        refreshed_id = refresh.id
        assert refresh.operation == "source-logo-refresh"
    original = logo_storage.store_logo_original
    monkeypatch.setattr(
        logo_storage,
        "store_logo_original",
        lambda client, url: {**original(client, url), "hash": "changed"},
    )
    monkeypatch.setattr(
        logo_storage,
        "store_logo_variant",
        lambda client, asset, size: {"key": f"logos/{asset['hash']}/{size}.webp", "width": size},
    )
    image_tasks.enrich_image(str(refreshed_id))
    with enabled() as session:
        assert session.get(Source, identifier).managed_logo == old
    image_tasks.enrich_image(str(refreshed_id))
    with enabled() as session:
        saved = session.get(Source, identifier)
        assert saved.managed_logo["hash"] == "changed"
        assert saved.logo_url == old["source_url"]
        assert logo_url(saved).endswith("changed/64.webp")
