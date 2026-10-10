import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_admin_api.topics import AdminTopicOut
from devfeed_aggregator import image_storage_tasks, image_tasks, logo_storage
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.image_jobs import retry_image
from devfeed_core.logos import logo_url
from devfeed_core.models import ArticleImageJob, Source, Topic, utcnow
from devfeed_core.schemas import SourceOut, SourcePublicOut
from devfeed_core.source_logos import backfill_source_logos, request_source_logo
from devfeed_core.topic_logos import backfill_topic_logos, request_topic_logo
from devfeed_core.topics import TopicOut
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.fixture(params=["topic", "source"])
def case(request):
    if request.param == "source":
        return SimpleNamespace(
            model=Source,
            job_field="source_id",
            sizes=(16, 32, 64, 96),
            fields={"source_type": "publisher", "approval_status": "approved"},
            request=request_source_logo,
            backfill=backfill_source_logos,
            public=SourcePublicOut,
            admin=SourceOut,
            fallback="https://remote.test/logo.svg",
        )
    return SimpleNamespace(
        model=Topic,
        job_field="topic_id",
        sizes=(32, 64, 96),
        fields={"kind": "tool", "status": "active"},
        request=request_topic_logo,
        backfill=backfill_topic_logos,
        public=TopicOut,
        admin=AdminTopicOut,
        fallback=None,
    )


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


def subject(factory, case):
    identifier = uuid.uuid4()
    with factory.begin() as session:
        session.add(
            case.model(
                id=identifier,
                name="Logo",
                slug=f"logo-{identifier}",
                **case.fields,
                **(
                    {"feed_url": f"https://remote.test/{identifier}.rss"}
                    if case.model is Source
                    else {}
                ),
                logo_url="https://remote.test/logo.svg",
            )
        )
    with factory() as session:
        job_id = session.scalar(
            select(ArticleImageJob.id).where(getattr(ArticleImageJob, case.job_field) == identifier)
        )
    return identifier, job_id


def process_logo(factory, job_id):
    image_tasks.enrich_image(str(job_id))
    with factory() as session:
        job = session.get(ArticleImageJob, job_id)
        resume = job.status == "queued" and job.storage and not job.error
    if resume:
        image_tasks.enrich_image(str(job_id))


def test_automatic_outbox_and_public_admin_contracts(enabled, case):
    identifier, job_id = subject(enabled, case)
    assert job_id
    with enabled.begin() as session:
        assert case.request(session, identifier).id == job_id
        assert case.backfill(session) == []
        assert (
            case.public.model_validate(session.get(case.model, identifier)).logo_url
            == case.fallback
        )
    process_logo(enabled, job_id)
    with enabled() as session:
        saved = session.get(case.model, identifier)
        assert saved.logo_url == "https://remote.test/logo.svg"
        output = case.public.model_validate(saved)
        assert output.logo_url == "https://images.test/topic-logos/v1/hash/64.webp"
        assert [v.width for v in output.logo_variants] == list(case.sizes)
        assert case.admin.model_validate(saved).logo_url == saved.logo_url
        assert session.get(ArticleImageJob, job_id).status == "succeeded"


def test_logo_edit_and_outbox_roll_back_together(enabled, case):
    identifier, job_id = subject(enabled, case)
    process_logo(enabled, job_id)
    with pytest.raises(RuntimeError, match="Roll back logo edit"), enabled.begin() as session:
        saved = session.get(case.model, identifier)
        saved.logo_url = "https://remote.test/rolled-back.svg"
        session.flush()
        replacement = case.request(session, identifier)
        assert replacement.id != job_id
        assert case.request(session, identifier).id == replacement.id
        raise RuntimeError("Roll back logo edit")
    with enabled() as session:
        assert session.get(case.model, identifier).logo_url == "https://remote.test/logo.svg"
        assert list(session.scalars(select(ArticleImageJob.id))) == [job_id]


def test_retry_reuses_checkpoint_and_only_publishes_complete_asset(enabled, case, monkeypatch):
    identifier, job_id = subject(enabled, case)
    calls = []

    def variant(client, asset, size):
        calls.append(size)
        if size == 64 and calls.count(64) == 1:
            raise FeedError("retry", reason="image_transform", retryable=True)
        return {"key": f"logo/{size}.webp", "width": size}

    monkeypatch.setattr(logo_storage, "store_logo_variant", variant)
    process_logo(enabled, job_id)
    with enabled.begin() as session:
        assert (
            logo_url(session.get(case.model, identifier))
            == "https://images.test/originals/hash.png"
        )
        job = session.get(ArticleImageJob, job_id)
        assert job.status == "queued"
        assert len(job.storage["variants"]) == case.sizes.index(64)
        job.available_at = utcnow() - timedelta(seconds=1)
    process_logo(enabled, job_id)
    assert calls == [*case.sizes[: case.sizes.index(64)], 64, 64, 96]
    with enabled() as session:
        assert logo_url(session.get(case.model, identifier)).endswith("64.webp")


def test_source_changes_during_processing_schedule_replacement(enabled, case, monkeypatch):
    identifier, job_id = subject(enabled, case)
    original = logo_storage.store_logo_original

    def change(client, url):
        with enabled.begin() as session:
            session.get(case.model, identifier).logo_url = "https://remote.test/new.svg"
        return original(client, url)

    monkeypatch.setattr(logo_storage, "store_logo_original", change)
    process_logo(enabled, job_id)
    with enabled() as session:
        assert not session.get(case.model, identifier).managed_logo
        assert session.get(ArticleImageJob, job_id).outcome == "already_present"
        replacement = session.scalar(
            select(ArticleImageJob).where(
                getattr(ArticleImageJob, case.job_field) == identifier,
                ArticleImageJob.status == "queued",
            )
        )
        assert replacement.image_url.endswith("new.svg")


def test_manual_retry_retains_completed_variants(enabled, case, monkeypatch):
    identifier, job_id = subject(enabled, case)

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
        assert len(new.storage["variants"]) == case.sizes.index(64)


def test_clear_and_replacement_failure_preserve_intent(enabled, case, monkeypatch):
    identifier, job_id = subject(enabled, case)
    process_logo(enabled, job_id)
    with enabled.begin() as session:
        saved = session.get(case.model, identifier)
        saved.logo_url = "https://remote.test/bad.svg"

    def fail(*args):
        raise FeedError("invalid", reason="invalid_topic_logo", retryable=False)

    monkeypatch.setattr(logo_storage, "store_logo_original", fail)
    with enabled() as session:
        replacement = session.scalar(
            select(ArticleImageJob.id).where(
                getattr(ArticleImageJob, case.job_field) == identifier,
                ArticleImageJob.status == "queued",
            )
        )
    process_logo(enabled, replacement)
    with enabled.begin() as session:
        saved = session.get(case.model, identifier)
        assert logo_url(saved).endswith("64.webp")
        saved.logo_url = None
        assert logo_url(saved) is None


def test_backfill_old_logos_is_bounded_and_idempotent(enabled, case, monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "false")
    get_settings.cache_clear()
    for _ in range(3):
        subject(enabled, case)
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "true")
    get_settings.cache_clear()
    with enabled.begin() as session:
        assert len(case.backfill(session, 2)) == 2
    with enabled.begin() as session:
        assert len(case.backfill(session, 2)) == 1
    with enabled.begin() as session:
        assert case.backfill(session, 2) == []


def test_backfill_skips_completed_assets_after_job_history_cleanup(enabled, case):
    from sqlalchemy import delete

    identifier, job_id = subject(enabled, case)
    process_logo(enabled, job_id)
    with enabled.begin() as session:
        session.execute(delete(ArticleImageJob).where(ArticleImageJob.id == job_id))
        assert case.backfill(session, 1) == []


def test_superseded_queued_job_schedules_current_source(enabled, case):
    identifier, job_id = subject(enabled, case)
    with enabled.begin() as session:
        session.get(case.model, identifier).logo_url = "https://remote.test/current.svg"
    process_logo(enabled, job_id)
    with enabled() as session:
        assert session.get(ArticleImageJob, job_id).outcome == "already_present"
        new = session.scalar(
            select(ArticleImageJob).where(
                getattr(ArticleImageJob, case.job_field) == identifier,
                ArticleImageJob.status == "queued",
            )
        )
        assert new.image_url.endswith("current.svg")


def test_admin_images_filter_and_retry_eligibility_include_logos(enabled, case):
    from devfeed_admin_api.jobs import jobs
    from devfeed_admin_api.pagination import ListQuery

    identifier, job_id = subject(enabled, case)
    other_id, _ = subject(enabled, case)
    with enabled.begin() as session:
        session.get(ArticleImageJob, job_id).status = "failed"
    with enabled() as session:
        result = jobs(
            "images",
            session,
            ListQuery(q="", sort=None, limit=25, offset=0),
            **{case.job_field: identifier},
            status="failed",
        )
        assert result["total"] == 1
        item = result["items"][0]
        assert getattr(item, case.job_field) == identifier
        assert item.retryable
        assert item.article_id is None
        assert item.target_name == "Logo"
    with enabled.begin() as session:
        retry_image(session, job_id)
    with enabled() as session:
        assert (
            jobs(
                "images",
                session,
                ListQuery(q="", sort=None, limit=25, offset=0),
                **{case.job_field: identifier},
                status="failed",
            )["total"]
            == 0
        )
        assert (
            jobs(
                "images",
                session,
                ListQuery(q="", sort=None, limit=25, offset=0),
                **{case.job_field: identifier},
                status="retried",
            )["total"]
            == 1
        )
        assert (
            jobs(
                "images",
                session,
                ListQuery(q="", sort=None, limit=25, offset=0),
                **{case.job_field: other_id},
            )["total"]
            == 1
        )


def test_concurrent_logo_edits_coalesce_outbox(enabled, case):
    from concurrent.futures import ThreadPoolExecutor

    identifier, job_id = subject(enabled, case)
    process_logo(enabled, job_id)

    def edit(index):
        with enabled.begin() as session:
            saved = session.get(case.model, identifier)
            saved.logo_url = f"https://remote.test/logo-{index}.svg"

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(edit, range(3)))
    with enabled() as session:
        active = session.scalars(
            select(ArticleImageJob).where(
                getattr(ArticleImageJob, case.job_field) == identifier,
                ArticleImageJob.status == "queued",
            )
        ).all()
        assert len(active) == 1


def test_import_pass_saves_all_originals_before_variant_pass(enabled, case, monkeypatch):
    first, first_job = subject(enabled, case)
    second, second_job = subject(enabled, case)
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
            assert logo_url(session.get(case.model, getattr(job, case.job_field))).endswith(
                "hash.png"
            )
    assert calls == ["original", "original"]
    with enabled() as session:
        assert (
            session.get(ArticleImageJob, first_job).available_at
            > session.get(ArticleImageJob, second_job).created_at
        )
    for identifier in (first_job, second_job):
        image_tasks.enrich_image(str(identifier))
    assert calls == ["original", "original", *(["variant"] * (2 * len(case.sizes)))]


@pytest.mark.parametrize("case", ["source"], indirect=True)
def test_explicit_refresh_imports_changed_artwork_at_same_url(enabled, case, monkeypatch):
    identifier, job_id = subject(enabled, case)
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
