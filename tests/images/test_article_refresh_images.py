"""Private image work keeps the approved publication intact across real DB lifecycles."""

import copy
import hashlib
import re
import uuid
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_aggregator import image_storage_tasks, image_tasks
from devfeed_core import article_refresh, image_jobs
from devfeed_core.article_jobs import approved_sources
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.managed_images import ARTICLE_THUMBNAIL_LAYOUT
from devfeed_core.models import (
    Article,
    ArticleContent,
    ArticleImageJob,
    ArticleOrigin,
    ArticleRefresh,
    Source,
    utcnow,
)
from devfeed_core.services import OperationConflict
from sqlalchemy import event, func, select, update

pytestmark = pytest.mark.integration
SOURCE = "https://publisher.example/original.png"


def asset(digest="a" * 64, widths=(320, 480, 640, 768, 960)):
    return {
        "source_url": SOURCE,
        "hash": digest,
        "original_key": f"originals/{digest}.png",
        "version": "v1",
        "source_width": 960,
        "thumbnail_version": "v2",
        "thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT,
        "variants": [
            {"width": width, "key": f"thumbnails/v2/{digest}/{width}.webp"} for width in widths
        ],
    }


@pytest.fixture
def maintenance(database, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "published_article_refresh_enabled", True)
    monkeypatch.setattr(settings, "image_storage_enabled", True)
    monkeypatch.setattr(settings, "image_public_url", "https://images.example")
    monkeypatch.setattr(image_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(image_storage_tasks, "storage_client", lambda: nullcontext(object()))
    promote = Mock(return_value="pending")
    monkeypatch.setattr(article_refresh, "try_promote_refresh", promote)
    return database, promote


def candidate(database, *, image=True):
    with database.begin() as session:
        source = Source(
            name="Approved image publisher",
            source_type="publisher",
            feed_url=f"https://publisher.example/{uuid.uuid4()}/feed",
            approval_status="approved",
            enabled=True,
        )
        url = f"https://publisher.example/{uuid.uuid4()}"
        article = Article(
            canonical_url=url,
            url_hash=uuid.uuid4().hex,
            title="Approved runtime article",
            summary="A published software engineering article.",
            ai_summary="The approved summary remains available during maintenance.",
            ai_description="An approved runtime guide.",
            language="en",
            review_status="approved",
            classification_provenance={"developer_relevance": "relevant", "page_kind": "article"},
            publication_status="published",
            published_to_feed_at=utcnow() - timedelta(days=2),
            image_url=SOURCE if image else None,
            managed_image=asset() if image else {},
            origins=[ArticleOrigin(source=source, entry_key="image", original_url=url)],
        )
        session.add(article)
        session.flush()
        body = "Private publisher extraction. " * 2000
        session.add(
            ArticleContent(
                article_id=article.id,
                url=article.canonical_url,
                text=body,
                content_hash=hashlib.sha256(body.encode()).hexdigest(),
                method="article",
            )
        )
        session.flush()
        prepared = article_refresh.prepare_refresh_base(session, article, {})
        approved_sources(session, article.id, lock=True)
        session.refresh(article, with_for_update=True)
        owner = article_refresh.create_refresh(
            session, article, prepared, "images", {"subject": "image-maintenance-test"}
        )
        return article.id, source.id, owner.id


def published(database, identifier):
    with database() as session:
        article = session.get(Article, identifier)
        return copy.deepcopy(
            (
                article.publication_status,
                article.review_status,
                article.image_url,
                article.managed_image,
                article.ai_summary,
                article.slug,
                article.published_to_feed_at,
            )
        )


@contextmanager
def image_queries():
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not any(re.search(r"article_refreshes\.payload(?:[,\s]|$)", sql) for sql in statements)


def test_storage_retry_keeps_public_receipt_and_never_loads_private_candidate_body(
    maintenance, monkeypatch
):
    database, promote = maintenance
    identifier, _, refresh_id = candidate(database)
    before = published(database, identifier)
    original = Mock(return_value=asset("b" * 64, ()))
    monkeypatch.setattr(image_storage_tasks.ImageOriginalStore, "store", original)
    attempted = []
    failed = False

    def variant(_store, receipt, width):
        nonlocal failed
        assert published(database, identifier) == before
        attempted.append(width)
        if width == 480 and not failed:
            failed = True
            raise FeedError("temporary transform", reason="image_transform", retryable=True)
        return {"width": width, "key": f"thumbnails/v2/{receipt['hash']}/{width}.webp"}

    monkeypatch.setattr(image_storage_tasks.ArticleThumbnailVariantStore, "store", variant)
    with image_queries():
        with database.begin() as session:
            job_id = image_jobs.request_image(
                session, identifier, refresh=True, refresh_id=refresh_id
            ).id
        image_tasks.enrich_image(str(job_id))
        with database.begin() as session:
            job = session.get(ArticleImageJob, job_id)
            assert job.status == "queued" and len(job.storage["variants"]) == 1
            assert (
                session.scalar(
                    select(ArticleRefresh.managed_image).where(ArticleRefresh.id == refresh_id)
                )
                == before[3]
            )
            job.available_at = utcnow() - timedelta(seconds=1)
        image_tasks.enrich_image(str(job_id))
    assert published(database, identifier) == before
    with database() as session:
        receipt = session.scalar(
            select(ArticleRefresh.managed_image).where(ArticleRefresh.id == refresh_id)
        )
        assert receipt == asset("b" * 64)
        assert session.get(ArticleImageJob, job_id).outcome == "found"
    assert original.call_count == 1
    assert attempted == [320, 480, 480, 640, 768, 960]
    promote.assert_called_once_with(database, refresh_id)


@pytest.mark.parametrize("change", ["source", "revision", "slot", "image"])
@pytest.mark.parametrize("when", ["claim", "checkpoint"])
def test_withdrawn_or_replaced_image_owner_never_applies_or_promotes(
    maintenance, monkeypatch, change, when
):
    database, promote = maintenance
    identifier, source_id, refresh_id = candidate(database)
    before = published(database, identifier)
    with database.begin() as session:
        job_id = image_jobs.request_image(
            session, identifier, refresh=True, refresh_id=refresh_id
        ).id

    def withdraw():
        with database.begin() as session:
            if change == "source":
                session.get(Source, source_id).approval_status = "rejected"
            elif change == "revision":
                session.get(Article, identifier).editorial_revision += 1
            elif change == "slot":
                session.execute(
                    update(ArticleRefresh)
                    .where(ArticleRefresh.id == refresh_id)
                    .values(storage_job_id=None)
                )
            else:
                session.execute(
                    update(ArticleRefresh)
                    .where(ArticleRefresh.id == refresh_id)
                    .values(image_url=SOURCE + "?replacement")
                )

    def original(_url):
        if when == "checkpoint":
            withdraw()
        return asset("b" * 64, ())

    store = Mock(side_effect=original)
    monkeypatch.setattr(image_storage_tasks.ImageOriginalStore, "store", store)
    if when == "claim":
        withdraw()
    with image_queries():
        image_tasks.enrich_image(str(job_id))
    assert published(database, identifier) == before
    with database() as session:
        assert session.get(ArticleImageJob, job_id).outcome == "already_present"
        assert (
            session.scalar(
                select(ArticleRefresh.managed_image).where(ArticleRefresh.id == refresh_id)
            )
            == before[3]
        )
    assert store.call_count == int(when == "checkpoint")
    promote.assert_not_called()


def test_discovery_selects_private_storage_without_changing_the_published_image(
    maintenance, monkeypatch
):
    database, promote = maintenance
    identifier, _, refresh_id = candidate(database, image=False)
    before = published(database, identifier)
    monkeypatch.setattr(
        image_tasks,
        "fetch_page",
        lambda url: FetchResult(
            200, f'<meta property="og:image" content="{SOURCE}">'.encode(), url
        ),
    )
    with image_queries():
        with database.begin() as session:
            discovery_id = image_jobs.request_image(session, identifier, refresh_id=refresh_id).id
        image_tasks.enrich_image(str(discovery_id))
    assert published(database, identifier) == before
    with database() as session:
        owner = session.get(ArticleRefresh, refresh_id)
        assert owner.image_url == SOURCE and owner.managed_image == {}
        assert session.get(ArticleImageJob, discovery_id).outcome == "found"
        storage = session.get(ArticleImageJob, owner.storage_job_id)
        assert storage.status == "queued" and storage.operation == "store"
        assert storage.refresh_id == refresh_id and storage.image_url == SOURCE
    promote.assert_called_once_with(database, refresh_id)


def test_foreign_active_image_work_is_never_adopted_into_a_candidate(maintenance):
    database, _ = maintenance
    identifier, _, refresh_id = candidate(database)
    with database.begin() as session:
        old = ArticleImageJob(article_id=identifier, operation="store", image_url=SOURCE)
        session.add(old)
        session.flush()
        old_id = old.id
    with pytest.raises(OperationConflict, match="Another image task"), database.begin() as session:
        image_jobs.request_image(session, identifier, refresh=True, refresh_id=refresh_id)
    with database() as session:
        assert session.get(ArticleImageJob, old_id).refresh_id is None
        assert session.scalar(select(func.count()).select_from(ArticleImageJob)) == 1


def test_image_retry_requires_the_selected_owner_and_preserves_previous_checkpoint(maintenance):
    database, _ = maintenance
    identifier, _, refresh_id = candidate(database)
    with database.begin() as session:
        previous = image_jobs.request_image(
            session, identifier, refresh=True, refresh_id=refresh_id
        )
        previous.status, previous.attempts = "failed", 3
        previous.storage = {**asset("b" * 64, (320,)), "article_refresh": True}
        previous_id = previous.id
        checkpoint = copy.deepcopy(previous.storage)
    with database.begin() as session:
        successor = image_jobs.retry_image(session, previous_id)
        assert successor.id != previous_id and successor.refresh_id == refresh_id
        assert successor.storage == checkpoint
        successor_id = successor.id
    with pytest.raises(OperationConflict, match="no longer owns"), database.begin() as session:
        image_jobs.retry_image(session, previous_id)
    with database() as session:
        previous = session.get(ArticleImageJob, previous_id)
        assert previous.status == "failed" and previous.attempts == 3
        assert previous.storage == checkpoint
        assert session.get(ArticleRefresh, refresh_id).storage_job_id == successor_id
