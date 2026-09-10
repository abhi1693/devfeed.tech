import json
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator import dispatch, image_tasks, scheduler
from devfeed_cli import images as image_cli
from devfeed_cli.main import run
from devfeed_core import image_jobs
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.job_lifecycle import fail_or_retry
from devfeed_core.models import Article, ArticleImageJob, utcnow
from devfeed_core.schemas import ImageJobOut
from devfeed_core.services import OperationConflict, RecordNotFound
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.dialects import postgresql


def job(**changes):
    return ArticleImageJob(
        **{
            "id": uuid.uuid4(),
            "article_id": uuid.uuid4(),
            "status": "queued",
            "attempts": 0,
            "available_at": utcnow() - timedelta(seconds=1),
            "created_at": utcnow(),
            **changes,
        }
    )


def session_with(*values):
    sequence = iter(values)
    statements, added = [], []

    def scalar(statement):
        statements.append(statement)
        return next(sequence)

    return SimpleNamespace(
        scalar=scalar, add=added.append, flush=lambda: None, statements=statements, added=added
    )


def test_requests_lock_article_coalesce_active_and_preserve_images():
    article = Article(id=uuid.uuid4(), image_url=None)
    active = job(article_id=article.id)
    session = session_with(article, active)
    assert image_jobs.request_image(session, article.id) is active
    assert not session.added
    assert "FOR UPDATE OF articles" in str(
        session.statements[0].compile(dialect=postgresql.dialect())
    )
    article.image_url = "https://cdn.example/existing.png"
    assert image_jobs.request_image(session_with(article), article.id) is None
    with pytest.raises(RecordNotFound):
        image_jobs.request_image(session_with(None), uuid.uuid4())


def test_automatic_discovery_never_repeats_completed_or_failed_lookup():
    article = Article(id=uuid.uuid4(), image_url=None)
    session = session_with(article, None, uuid.uuid4())
    assert image_jobs.request_image(session, article.id, automatic=True) is None
    assert not session.added
    session = session_with(article, None, None)
    result = image_jobs.request_image(session, article.id, automatic=True)
    assert result.article_id == article.id and session.added == [result]
    # Explicit fetch may schedule another lookup after no-image or a terminal failure.
    assert (
        image_jobs.request_image(session_with(article, None), article.id).article_id == article.id
    )


def test_backfill_filters_before_limit_and_skips_locks(monkeypatch):
    identifier = uuid.uuid4()
    statements = []

    def scalars(statement):
        statements.append(statement)
        return SimpleNamespace(all=lambda: [identifier])

    requests = []
    monkeypatch.setattr(
        image_jobs,
        "request_image",
        lambda session, aid, **kw: requests.append((aid, kw)) or job(article_id=aid),
    )
    result = image_jobs.backfill_images(SimpleNamespace(scalars=scalars), 23)
    assert len(result) == 1 and requests == [(identifier, {"automatic": True})]
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "articles.image_url IS NULL" in sql and "NOT (EXISTS" in sql
    assert "SKIP LOCKED" in sql and "LIMIT" in sql
    for invalid in (0, 501):
        with pytest.raises(ValueError):
            image_jobs.backfill_images(None, invalid)


@pytest.mark.parametrize("state", ["running", "succeeded", "failed", "delayed"])
def test_claim_cannot_steal_or_repeat_job(state):
    current = job(status="queued" if state == "delayed" else state)
    if state == "delayed":
        current.available_at = utcnow() + timedelta(minutes=10)
    assert image_jobs.claim_image(session_with(current), current.id) is None
    assert current.attempts == 0


def test_claim_has_lease_and_skips_fetch_if_publisher_supplied_an_image():
    current = job()
    article = Article(canonical_url="https://publisher.example/article", image_url=None)
    session = session_with(current)
    session.get = lambda *a, **kw: article
    assert image_jobs.claim_image(session, current.id) == (current, article.canonical_url)
    assert current.attempts == 1 and current.lease_token and current.lease_until > utcnow()
    current = job()
    session = session_with(current)
    article.image_url = "https://cdn.example/cover.png"
    session.get = lambda *a, **kw: article
    assert image_jobs.claim_image(session, current.id) is None
    assert current.status == "succeeded" and current.outcome == "already_present"


def test_retry_cap_retry_after_and_permanent_failures():
    current = job(attempts=1, status="running", lease_token=uuid.uuid4())
    fail_or_retry(current, "temporary", utcnow(), retry_after=120)
    assert current.status == "queued" and current.available_at > utcnow() + timedelta(seconds=110)
    assert current.lease_token is None and current.dispatched_at is None
    current.attempts = 3
    fail_or_retry(current, "exhausted", utcnow())
    assert current.status == "failed" and current.finished_at
    current = job(attempts=1)
    fail_or_retry(current, "permanent", utcnow(), retryable=False)
    assert current.status == "failed"


def test_explicit_retry_keeps_old_history_and_rejects_other_states(monkeypatch):
    previous = job(status="failed", error="previous failure")
    session = SimpleNamespace(get=lambda *a: previous)
    calls = []
    replacement = job(article_id=previous.article_id)
    monkeypatch.setattr(
        image_jobs, "request_image", lambda s, aid: calls.append(aid) or replacement
    )
    assert image_jobs.retry_image(session, previous.id) is replacement
    assert previous.status == "failed" and previous.error == "previous failure"
    assert calls == [previous.article_id]
    previous.status = "succeeded"
    with pytest.raises(OperationConflict):
        image_jobs.retry_image(session, previous.id)
    with pytest.raises(RecordNotFound):
        image_jobs.retry_image(SimpleNamespace(get=lambda *a: None), uuid.uuid4())


def test_force_dispatch_preserves_attempts_and_rejects_running_job():
    current = job(attempts=2, dispatched_at=utcnow(), available_at=utcnow() + timedelta(minutes=10))
    image_jobs.prepare_image_dispatch(session_with(current), current.id)
    assert (
        current.dispatched_at is None and current.available_at <= utcnow() and current.attempts == 2
    )
    current.status = "running"
    with pytest.raises(OperationConflict):
        image_jobs.prepare_image_dispatch(session_with(current), current.id)


@pytest.fixture
def image_runtime(monkeypatch):
    current = job(status="running", attempts=1, lease_token=uuid.uuid4())
    events, statements = [], []

    def scalar(statement):
        statements.append(statement)
        return current.article_id if statement.is_update else current

    session = SimpleNamespace(scalar=scalar)

    @contextmanager
    def begin():
        events.append("begin")
        yield session
        events.append("commit")

    factory = SimpleNamespace(begin=begin)
    monkeypatch.setattr(image_tasks, "session_factory", lambda: factory)
    monkeypatch.setattr(
        image_tasks, "claim_image", lambda *a: (current, "https://publisher.example/article")
    )

    def fetch(url):
        assert events == ["begin", "commit"]
        events.append("fetch")
        return FetchResult(
            200,
            b'<meta property="og:image" content="https://cdn.example/cover.png?key=private-token">',
            url,
        )

    monkeypatch.setattr(image_tasks, "fetch_page", fetch)
    return current, events, statements


def test_worker_fetches_outside_transaction_and_updates_only_missing_image(image_runtime, caplog):
    current, events, statements = image_runtime
    with caplog.at_level("INFO"):
        image_tasks.enrich_image(str(current.id))
    assert events == ["begin", "commit", "fetch", "begin", "commit"]
    assert (current.status, current.outcome, current.method) == ("succeeded", "found", "og:image")
    assert current.lease_token is None
    sql = str(statements[-1].compile(dialect=postgresql.dialect()))
    assert "articles.image_url IS NULL" in sql and "SET image_url=" in sql
    assert "feed_at" not in sql and "title" not in sql
    assert "private-token" not in caplog.text


def test_worker_does_not_overwrite_if_image_arrived_during_fetch(image_runtime, monkeypatch):
    current, _, _ = image_runtime

    @contextmanager
    def begin():
        yield SimpleNamespace(scalar=lambda stmt: None if stmt.is_update else current)

    monkeypatch.setattr(image_tasks, "session_factory", lambda: SimpleNamespace(begin=begin))
    monkeypatch.setattr(
        image_tasks,
        "fetch_page",
        lambda url: FetchResult(200, b'<meta property="og:image" content="/cover.png">', url),
    )
    image_tasks.enrich_image(str(current.id))
    assert current.outcome == "already_present"


def test_no_image_is_success_without_updates_or_retry(image_runtime, monkeypatch):
    current, _, statements = image_runtime
    monkeypatch.setattr(
        image_tasks, "fetch_page", lambda url: FetchResult(200, b'<img src="/logo.png">', url)
    )
    image_tasks.enrich_image(str(current.id))
    assert (current.status, current.outcome, current.error) == ("succeeded", "not_found", None)
    assert not any(statement.is_update for statement in statements)


@pytest.mark.parametrize("retryable,expected", [(True, "queued"), (False, "failed")])
def test_worker_failure_persists_safe_retry_decision(
    image_runtime, monkeypatch, retryable, expected
):
    current, _, _ = image_runtime

    def fetch(url):
        raise FeedError(
            "private-page-data",
            retryable=retryable,
            status=503 if retryable else 404,
            retry_after=120,
            reason="http_error",
        )

    monkeypatch.setattr(image_tasks, "fetch_page", fetch)
    image_tasks.enrich_image(str(current.id))
    assert current.status == expected and current.error == "Image lookup failed: http_error"
    assert current.lease_token is None


def test_stale_worker_cannot_store_image_or_finish_job(image_runtime, monkeypatch):
    current, _, statements = image_runtime

    def fetch(url):
        current.lease_token = uuid.uuid4()
        return FetchResult(200, b'<meta property="og:image" content="/cover.png">', url)

    monkeypatch.setattr(image_tasks, "fetch_page", fetch)
    image_tasks.enrich_image(str(current.id))
    assert current.status == "running" and current.outcome is None
    assert not any(statement.is_update for statement in statements)


def test_scheduler_dispatches_image_function_with_json_compatible_id():
    current, calls, events = job(), [], []

    @contextmanager
    def begin():
        yield session_with(current)
        events.append("commit")

    def enqueue(*args, **kw):
        calls.append((args, kw))
        events.append("publish")
        return SimpleNamespace(id="rq-job")

    assert (
        scheduler.dispatch_jobs(
            SimpleNamespace(begin=begin),
            SimpleNamespace(enqueue=enqueue),
            1,
            utcnow(),
            kind="images",
        )
        == 1
    )
    assert calls[0][0] == ("devfeed_aggregator.image_tasks.enrich_image", str(current.id))
    assert calls[0][1]["job_timeout"] == 180 and calls[0][1]["ttl"] == 300
    assert events == ["publish", "commit"] and current.dispatched_at is not None


def test_scheduler_recovers_expired_image_lease():
    current = job(status="running", attempts=1, lease_token=uuid.uuid4())

    @contextmanager
    def begin():
        yield SimpleNamespace(scalars=lambda stmt: SimpleNamespace(all=lambda: [current]))

    assert scheduler.recover_jobs(SimpleNamespace(begin=begin), 100, utcnow(), kind="images") == 1
    assert current.status == "queued" and current.lease_token is None


@pytest.mark.parametrize(
    "args", [["images"], ["images", "fetch"], ["images", "backfill"], ["images", "jobs"]]
)
def test_image_help_never_starts_services_or_needs_configuration(args, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEVFEED_DATABASE_URL")
    monkeypatch.delenv("DEVFEED_REDIS_URL")
    with pytest.raises(SystemExit) as result:
        run([*args, "--help"])
    assert result.value.code == 0


@pytest.fixture
def immediate_image(monkeypatch):
    current = job(available_at=utcnow() + timedelta(minutes=5), dispatched_at=utcnow())
    events = []
    session = SimpleNamespace(
        scalar=lambda stmt: current, get=lambda *a: current, flush=lambda: None
    )

    @contextmanager
    def begin():
        events.append("begin")
        yield session
        events.append("commit")

    @contextmanager
    def read():
        yield session

    class Factory:
        def begin(self):
            return begin()

        def __call__(self):
            return read()

    factory = Factory()
    queue = SimpleNamespace(connection=SimpleNamespace(close=lambda: events.append("close")))
    monkeypatch.setattr(dispatch, "session_factory", lambda: factory)
    monkeypatch.setattr(dispatch, "get_queue", lambda: queue)
    return current, events, factory, queue


def test_immediate_image_dispatch_commits_before_broker_and_only_targets_image_job(
    immediate_image, monkeypatch
):
    current, events, factory, queue = immediate_image

    def publish(actual_factory, actual_queue, batch, now, *, job_id, kind):
        assert events == ["begin", "commit"]
        assert (actual_factory, actual_queue, batch, job_id, kind) == (
            factory,
            queue,
            1,
            current.id,
            "images",
        )
        assert current.available_at <= now and current.dispatched_at is None
        current.dispatched_at = now
        return 1

    monkeypatch.setattr(dispatch, "dispatch_jobs", publish)
    result = dispatch.dispatch_now(current.id, kind="images")
    assert result["id"] == str(current.id) and result["dispatched_at"] is not None
    assert events[-1] == "close"


def test_immediate_image_broker_failure_leaves_durable_job_ready(immediate_image, monkeypatch):
    current, events, _, _ = immediate_image

    def fail(*a, **kw):
        assert events == ["begin", "commit"]
        raise RedisConnectionError("private-connection-string")

    monkeypatch.setattr(dispatch, "dispatch_jobs", fail)
    with pytest.raises(RedisConnectionError):
        dispatch.dispatch_now(current.id, kind="images")
    assert current.status == "queued" and current.available_at <= utcnow()
    assert current.dispatched_at is None and events[-1] == "close"


def test_cli_image_fetch_force_commits_then_dispatches_without_network(
    immediate_image, monkeypatch, capsys
):
    current, events, factory, _ = immediate_image
    monkeypatch.setattr(image_cli, "session_factory", lambda: factory)
    monkeypatch.setattr(image_cli, "request_image", lambda *a: current)

    def publish(identifier, *, kind):
        assert kind == "images"
        assert events == ["begin", "commit"] and identifier == current.id
        return ImageJobOut.model_validate(current).model_dump(mode="json")

    monkeypatch.setattr(image_cli, "dispatch_now", publish)
    assert run(["images", "fetch", str(current.article_id), "--force"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["article_id"] == str(current.article_id) and result["job"]["id"] == str(
        current.id
    )
    assert result["already_present"] is False


def test_cli_existing_image_does_not_dispatch_or_backfill_it(immediate_image, monkeypatch, capsys):
    current, _, factory, _ = immediate_image
    monkeypatch.setattr(image_cli, "session_factory", lambda: factory)
    monkeypatch.setattr(image_cli, "request_image", lambda *a: None)
    monkeypatch.setattr(
        image_cli, "dispatch_now", lambda *a: pytest.fail("Dispatched an existing image")
    )
    assert run(["images", "fetch", str(current.article_id), "--force"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["already_present"] is True and result["job"] is None


def test_cli_backfill_only_queues_bounded_jobs(immediate_image, monkeypatch, capsys):
    current, _, factory, _ = immediate_image
    calls = []
    monkeypatch.setattr(image_cli, "session_factory", lambda: factory)
    monkeypatch.setattr(
        image_cli, "backfill_images", lambda session, limit: calls.append(limit) or [current]
    )
    monkeypatch.setattr(image_tasks, "fetch_page", lambda *a: pytest.fail("Fetched in CLI"))
    assert run(["images", "backfill", "--limit", "23"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == [23] and result["queued"] == 1 and len(result["jobs"]) == 1
    with pytest.raises(SystemExit) as error:
        run(["images", "backfill", "--limit", "501"])
    assert error.value.code == 2
