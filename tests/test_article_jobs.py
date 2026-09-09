import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator import article_tasks, scheduler
from devfeed_aggregator.article_pages import PageArticle
from devfeed_core import article_jobs
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.models import Article, ArticleEnrichmentJob, utcnow
from devfeed_core.services import OperationConflict, RecordNotFound
from sqlalchemy.dialects import postgresql


def job(**values):
    fields = dict(
        id=uuid.uuid4(),
        article_id=uuid.uuid4(),
        status="queued",
        attempts=0,
        available_at=utcnow(),
        changed_fields=[],
        result={},
    )
    return ArticleEnrichmentJob(**{**fields, **values})


def session_with(*rows):
    values, statements, added = iter(rows), [], []

    def scalar(statement):
        statements.append(statement)
        return next(values)

    return SimpleNamespace(
        scalar=scalar, statements=statements, add=added.append, added=added, flush=lambda: None
    )


@pytest.fixture(autouse=True)
def approved(monkeypatch):
    monkeypatch.setattr(article_jobs, "approved_sources", lambda *a, **k: [uuid.uuid4()])


def test_request_coalesces_active_jobs_and_does_not_depend_on_missing_image():
    article = Article(
        id=uuid.uuid4(), metadata_source_type="aggregator", image_url="https://example.com/image"
    )
    active = job(article_id=article.id)
    session = session_with(article, active)
    assert article_jobs.request_article_enrichment(session, article.id) is active
    assert not session.added
    assert "FOR UPDATE OF articles" in str(
        session.statements[0].compile(dialect=postgresql.dialect())
    )
    result = article_jobs.request_article_enrichment(session_with(article, None), article.id)
    assert result.article_id == article.id


def test_missing_article_is_rejected_and_publisher_gaps_can_be_queued():
    with pytest.raises(RecordNotFound):
        article_jobs.request_article_enrichment(session_with(None), uuid.uuid4())
    article = Article(id=uuid.uuid4(), metadata_source_type="publisher")
    result = article_jobs.request_article_enrichment(session_with(article, None), article.id)
    assert result.article_id == article.id


def test_automatic_requests_do_not_repeat_terminal_lookups_and_reject_unapproved(monkeypatch):
    article = Article(id=uuid.uuid4(), metadata_source_type="aggregator")
    session = session_with(article, None, uuid.uuid4())
    assert article_jobs.request_article_enrichment(session, article.id, automatic=True) is None
    assert not session.added
    monkeypatch.setattr(article_jobs, "approved_sources", lambda *a, **k: [])
    with pytest.raises(OperationConflict, match="approved source"):
        article_jobs.request_article_enrichment(session_with(article), article.id)


def test_backfill_filters_eligible_origins_and_attempts_before_limit(monkeypatch):
    statements = []
    identifier, source_id = uuid.uuid4(), uuid.uuid4()

    def scalars(statement):
        statements.append(statement.compile(dialect=postgresql.dialect()))
        return SimpleNamespace(all=lambda: [identifier])

    requested = []
    monkeypatch.setattr(
        article_jobs,
        "request_article_enrichment",
        lambda s, aid, **kw: requested.append((aid, kw)) or job(article_id=aid),
    )
    result = article_jobs.backfill_articles(
        SimpleNamespace(scalars=scalars), 3, source_id=source_id
    )
    assert len(result) == 1 and requested == [(identifier, {"automatic": True})]
    sql = str(statements[0])
    assert "NOT (EXISTS" in sql and "SKIP LOCKED" in sql and "LIMIT" in sql
    assert "approval_status" in sql and source_id in statements[0].params.values()
    assert "image_url IS NULL" not in sql
    for invalid in (0, 501):
        with pytest.raises(ValueError):
            article_jobs.backfill_articles(None, invalid)


@pytest.mark.parametrize("status", ["running", "succeeded", "failed", "delayed"])
def test_claim_cannot_steal_or_repeat_work(status):
    current = job(status="queued" if status == "delayed" else status)
    if status == "delayed":
        current.available_at = utcnow() + timedelta(minutes=5)
    assert article_jobs.claim_article(session_with(current), current.id) is None
    assert current.attempts == 0


def test_claim_leases_and_checks_publisher_supersession_and_approval(monkeypatch):
    current = job()
    article = Article(
        id=current.article_id,
        metadata_source_type="aggregator",
        canonical_url="https://example.com/article",
    )
    session = session_with(current)
    session.get = lambda *a, **k: article
    assert article_jobs.claim_article(session, current.id) == (current, article.canonical_url)
    assert current.status == "running" and current.attempts == 1
    assert current.lease_token and current.lease_until > utcnow()
    for kind, outcome in [("publisher", "unapproved"), ("aggregator", "unapproved")]:
        current = job()
        article.metadata_source_type = kind
        session = session_with(current)
        session.get = lambda *a, **k: article
        monkeypatch.setattr(article_jobs, "approved_sources", lambda *a, **k: [])
        assert article_jobs.claim_article(session, current.id) is None
        assert current.status == "succeeded" and current.outcome == outcome


def test_bounded_retries_honor_retry_after_and_retain_history(monkeypatch):
    current = job(attempts=1, status="running", lease_token=uuid.uuid4())
    article_jobs.fail_article(current, "temporary", retry_after=120)
    assert current.status == "queued" and current.available_at > utcnow() + timedelta(seconds=110)
    assert current.lease_token is None and current.dispatched_at is None
    current.attempts = 3
    article_jobs.fail_article(current, "exhausted")
    assert current.status == "failed" and current.finished_at
    replacement = job(article_id=current.article_id)
    monkeypatch.setattr(article_jobs, "request_article_enrichment", lambda *a: replacement)
    assert (
        article_jobs.retry_article(SimpleNamespace(get=lambda *a: current), current.id)
        is replacement
    )
    assert current.error == "exhausted" and current.status == "failed"
    current.status = "succeeded"
    with pytest.raises(OperationConflict):
        article_jobs.retry_article(SimpleNamespace(get=lambda *a: current), current.id)


def test_force_dispatch_does_not_reset_attempts_or_steal_running_lease():
    current = job(attempts=2, available_at=utcnow() + timedelta(minutes=5))
    article_jobs.prepare_article_dispatch(session_with(current), current.id)
    assert current.available_at <= utcnow() and current.attempts == 2
    current.status = "running"
    with pytest.raises(OperationConflict):
        article_jobs.prepare_article_dispatch(session_with(current), current.id)


@pytest.fixture
def runtime(monkeypatch):
    current = job(status="running", attempts=1, lease_token=uuid.uuid4())
    article = Article(
        id=current.article_id,
        metadata_source_type="aggregator",
        canonical_url="https://example.com/article",
    )
    events, changes = [], []

    @contextmanager
    def begin():
        events.append("begin")
        yield SimpleNamespace(
            scalar=lambda stmt: (
                current
                if stmt.column_descriptions[0]["entity"] is ArticleEnrichmentJob
                else article
            )
        )
        events.append("commit")

    def fetch(url):
        assert events == ["begin", "commit"]
        events.append("fetch")
        return FetchResult(200, b"<html></html>", url)

    def extract(result, now):
        assert events == ["begin", "commit", "fetch"]
        return PageArticle(
            "Original", "Summary", "Writer", None, None, "en", "body", {"language": "en"}
        )

    monkeypatch.setattr(article_tasks, "session_factory", lambda: SimpleNamespace(begin=begin))
    monkeypatch.setattr(article_tasks, "claim_article", lambda *a: (current, article.canonical_url))
    monkeypatch.setattr(article_tasks, "fetch_article_page", fetch)
    monkeypatch.setattr(article_tasks, "extract_article", extract)
    monkeypatch.setattr(article_tasks, "approved_sources", lambda *a, **k: [uuid.uuid4()])
    monkeypatch.setattr(
        article_tasks, "apply_page", lambda *a: changes.append(True) or ["summary", "language"]
    )
    return current, article, events, changes


def test_worker_download_and_extraction_are_outside_transaction(runtime):
    current, _, events, changes = runtime
    article_tasks.enrich_article(str(current.id))
    assert events == ["begin", "commit", "fetch", "begin", "commit"]
    assert changes == [True] and current.status == "succeeded" and current.outcome == "enriched"
    assert current.lease_token is None and current.result == {"language": "en"}


@pytest.mark.parametrize("case", ["lease", "url", "review"])
def test_worker_checks_concurrent_changes_before_writing(runtime, monkeypatch, case):
    current, article, _, changes = runtime
    original = article_tasks.fetch_article_page

    def fetch(url):
        if case == "lease":
            current.lease_token = uuid.uuid4()
        if case == "url":
            article.canonical_url += "-changed"
        if case == "review":
            monkeypatch.setattr(article_tasks, "approved_sources", lambda *a, **k: [])
        return original(url)

    monkeypatch.setattr(article_tasks, "fetch_article_page", fetch)
    article_tasks.enrich_article(str(current.id))
    assert not changes
    assert current.status == ("running" if case == "lease" else "succeeded")
    if case != "lease":
        assert current.outcome == ("unapproved" if case == "review" else "superseded")


@pytest.mark.parametrize("retryable,status", [(True, 503), (False, 404)])
def test_fetch_failure_is_independent_safe_and_visible(runtime, monkeypatch, retryable, status):
    current, _, _, changes = runtime

    def fetch(_):
        raise FeedError(
            "secret page data",
            reason="http_error",
            status=status,
            retryable=retryable,
            retry_after=120,
        )

    monkeypatch.setattr(article_tasks, "fetch_article_page", fetch)
    article_tasks.enrich_article(str(current.id))
    assert current.status == ("queued" if retryable else "failed")
    assert current.error == "Article lookup failed: http_error" and current.http_status == status
    assert not changes


def test_oversized_article_explains_limit_without_leaking_response(runtime, monkeypatch):
    current, _, _, changes = runtime

    def fetch(_):
        raise FeedError(
            "secret page data",
            reason="response_too_large",
            status=200,
            limit_bytes=10_000_000,
        )

    monkeypatch.setattr(article_tasks, "fetch_article_page", fetch)
    article_tasks.enrich_article(str(current.id))
    assert current.status == "failed" and current.http_status == 200
    assert "10,000,000-byte" in current.error
    assert "DEVFEED_ARTICLE_PAGE_MAX_BYTES" in current.error
    assert "secret" not in current.error and not changes


def test_scheduler_dispatches_and_recovers_article_jobs():
    current, calls = job(), []

    @contextmanager
    def begin():
        yield session_with(current)

    queue = SimpleNamespace(
        enqueue=lambda *a, **kw: calls.append((a, kw)) or SimpleNamespace(id="rq-id")
    )
    assert (
        scheduler.dispatch_jobs(SimpleNamespace(begin=begin), queue, 1, utcnow(), articles=True)
        == 1
    )
    assert calls[0][0] == ("devfeed_aggregator.article_tasks.enrich_article", str(current.id))
    assert calls[0][1]["job_timeout"] == 180 and current.dispatched_at
    with pytest.raises(ValueError):
        scheduler.dispatch_jobs(None, None, 1, utcnow(), images=True, articles=True)

    @contextmanager
    def recovery():
        yield SimpleNamespace(scalars=lambda _: SimpleNamespace(all=lambda: [current]))

    current.status, current.attempts, current.lease_token = "running", 1, uuid.uuid4()
    assert scheduler.recover_article_jobs(SimpleNamespace(begin=recovery), 10, utcnow()) == 1
    assert current.status == "queued" and current.lease_token is None
