"""Delivery-before-commit regression on explicitly supplied disposable test services."""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from devfeed_core.article_jobs import claim_article
from devfeed_core.image_jobs import claim_image
from devfeed_core.job_dispatch import dispatch_jobs
from devfeed_core.jobs import claim_job, owned_job
from devfeed_core.models import (
    Article,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleOrigin,
    IngestionJob,
    Source,
    SourceEnrichmentJob,
    utcnow,
)
from devfeed_core.source_enrichment import claim_enrichment
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "model,claim,options",
    [
        (IngestionJob, claim_job, {}),
        (ArticleImageJob, claim_image, {"kind": "images"}),
        (SourceEnrichmentJob, claim_enrichment, {"kind": "source-enrichment"}),
        (ArticleEnrichmentJob, claim_article, {"kind": "article-enrichment"}),
    ],
)
def test_worker_consuming_before_dispatch_commit_waits_and_claims(database, model, claim, options):
    with database.begin() as session:
        source = Source(
            name="Community",
            feed_url="https://example.com/rss",
            source_type="aggregator",
            approval_status="approved",
        )
        article = Article(
            canonical_url="https://example.com/article",
            url_hash="test-hash",
            title="Article",
            summary="",
            metadata_source_type="aggregator",
        )
        session.add_all([source, article])
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key="entry",
                original_url=article.canonical_url,
            )
        )
        kwargs = (
            {"source_id": source.id}
            if model in {IngestionJob, SourceEnrichmentJob}
            else {"article_id": article.id}
        )
        job = model(**kwargs)
        session.add(job)
        session.flush()
        identifier = job.id

    ready = Event()
    worker_pids, futures = [], []

    def consume():
        with database.begin() as session:
            worker_pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            claimed = claim(session, identifier)
            return claimed[0].id if claimed else None

    with ThreadPoolExecutor(max_workers=1) as pool:

        def enqueue(*args, **kwargs):
            # A worker can receive this message while dispatch_jobs still owns the
            # row lock. Detect a real PostgreSQL lock wait, not just thread timing.
            future = pool.submit(consume)
            futures.append(future)
            assert ready.wait(5)
            deadline = time.monotonic() + 5
            with database() as observer:
                while time.monotonic() < deadline:
                    assert not future.done(), (
                        "Worker discarded a delivery while dispatch was locked"
                    )
                    blocked_by = observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": worker_pids[0]}
                    )
                    if blocked_by:
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("Worker never waited for the dispatch row lock")
            return SimpleNamespace(id="simulated-rq-delivery")

        assert (
            dispatch_jobs(
                database,
                SimpleNamespace(enqueue=enqueue),
                1,
                utcnow(),
                job_id=identifier,
                **options,
            )
            == 1
        )
        assert futures[0].result(timeout=5) == identifier

    with database.begin() as session:
        saved = session.get(model, identifier)
        assert saved.status == "running" and saved.attempts == 1 and saved.lease_token
        assert saved.dispatched_at is not None
        assert claim(session, identifier) is None  # Duplicate delivery cannot steal the lease.


def test_finishing_worker_waits_for_recovery_and_rejects_its_replaced_lease(database):
    original_token, replacement_token = uuid.uuid4(), uuid.uuid4()
    with database.begin() as session:
        article = Article(
            canonical_url="https://example.com/ownership",
            url_hash="ownership-test",
            title="Ownership",
            summary="",
        )
        session.add(article)
        session.flush()
        job = ArticleImageJob(
            article_id=article.id, status="running", attempts=1, lease_token=original_token
        )
        session.add(job)
        session.flush()
        identifier = job.id

    ready = Event()
    worker_pids = []

    def finish_old_attempt():
        with database.begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            worker_pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            ready.set()
            return owned_job(session, ArticleImageJob, identifier, original_token)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.begin() as recovery:
            job = recovery.get(ArticleImageJob, identifier, with_for_update=True)
            job.lease_token = replacement_token
            recovery.flush()
            future = pool.submit(finish_old_attempt)
            assert ready.wait(5)
            deadline = time.monotonic() + 5
            with database() as observer:
                while time.monotonic() < deadline:
                    assert not future.done(), "Finishing worker did not wait for recovery"
                    if observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": worker_pids[0]}
                    ):
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("Finishing worker never waited for the recovery lock")
        assert future.result(timeout=5) is None

    with database.begin() as session:
        current = owned_job(session, ArticleImageJob, identifier, replacement_token)
        assert current is not None and current.attempts == 1
        assert current.status == "running" and current.error is None
