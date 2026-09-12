import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator import dispatch, scheduler, source_tasks, tasks
from devfeed_cli.main import run
from devfeed_core import jobs, services, source_enrichment
from devfeed_core.job_lifecycle import fail_or_retry
from devfeed_core.models import IngestionJob, Source, SourceEnrichmentJob, SourceReview, utcnow
from devfeed_core.schemas import SourceDecision
from sqlalchemy.dialects import postgresql


def source(status="pending", **fields):
    return Source(
        id=uuid.uuid4(),
        name="Example",
        feed_url="https://example.com/rss",
        source_type="publisher",
        enabled=True,
        approval_status=status,
        poll_interval_seconds=1800,
        **fields,
    )


def session_with(*rows):
    values = iter(rows)
    added, statements = [], []

    def scalar(stmt):
        statements.append(stmt)
        return next(values)

    return SimpleNamespace(
        scalar=scalar, add=added.append, added=added, statements=statements, flush=lambda: None
    )


def test_api_creation_is_pending_and_never_queues_any_jobs():
    session = session_with()
    record = services.create_source(
        session,
        services.ValidatedSource("Name", "https://example.com/rss", "publisher", True, 1800),
    )
    assert record.approval_status == "pending" and record.submission_channel == "api"
    assert session.added == [record] and not session.statements


def test_trusted_cli_creation_is_approved_with_review_history(monkeypatch):
    record = source("approved")
    session = session_with(record.id, record)
    calls = []
    monkeypatch.setattr(
        source_enrichment, "request_enrichment", lambda s, sid: calls.append(("profile", sid))
    )
    monkeypatch.setattr(
        services, "request_ingestion", lambda s, item: calls.append(("ingestion", item.id)) or "job"
    )
    result = services.submit_source(
        session, services.ValidatedSource("Name", record.feed_url, "publisher", True, 1800)
    )
    assert result == (record, True, "job")
    assert {name for name, _ in calls} == {"profile", "ingestion"}
    assert isinstance(session.added[0], SourceReview) and session.added[0].decision == "approved"
    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    assert (
        compiled.params["approval_status"] == "approved"
        and compiled.params["submission_channel"] == "cli"
    )


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_cli_resubmission_never_approves_existing_source(status, monkeypatch):
    record = source(status)
    session = session_with(None, record)
    monkeypatch.setattr(
        services, "request_ingestion", lambda *a: pytest.fail("Queued unapproved source")
    )
    assert services.submit_source(
        session, services.ValidatedSource("Replacement", record.feed_url, "publisher", True, 1800)
    ) == (record, False, None)
    assert record.approval_status == status and record.name == "Example" and not session.added


def test_approval_records_actor_and_queues_work_but_repetition_is_idempotent(monkeypatch):
    record = source()
    calls = []
    monkeypatch.setattr(services, "request_ingestion", lambda *a: calls.append("ingest"))
    monkeypatch.setattr(source_enrichment, "request_enrichment", lambda *a: calls.append("enrich"))
    session = session_with(record, record)
    decision = SourceDecision(decision="approved", actor="Operator", note="Reviewed")
    assert services.review_source(session, record.id, decision) is record
    assert record.enabled and record.reviewed_by == "Operator" and record.reviewed_at
    assert calls == ["ingest", "enrich"]
    assert session.added[0].actor == "Operator"
    services.review_source(session, record.id, decision)
    assert calls == ["ingest", "enrich"] and len(session.added) == 1


def test_approval_can_leave_source_disabled_without_queuing_work(monkeypatch):
    record = source()
    session = session_with(record)
    monkeypatch.setattr(
        services, "request_ingestion", lambda *a: pytest.fail("Queued disabled source")
    )
    monkeypatch.setattr(
        source_enrichment, "request_enrichment", lambda *a: pytest.fail("Queued disabled source")
    )
    services.review_source(
        session,
        record.id,
        SourceDecision(decision="approved", actor="Operator"),
        enable_on_approval=False,
    )
    assert record.approval_status == "approved" and not record.enabled
    assert record.reviewed_by == "Operator" and record.reviewed_at
    assert len(session.added) == 1 and session.added[0].decision == "approved"


def test_rejection_needs_reason_disables_source_and_retains_first_submitter():
    record = source("approved", submitted_by={"name": "First contributor"})
    with pytest.raises(services.OperationConflict):
        services.review_source(session_with(record), record.id, SourceDecision(decision="rejected"))
    session = session_with(record)
    services.review_source(
        session, record.id, SourceDecision(decision="rejected", note="Not relevant")
    )
    assert record.approval_status == "rejected" and not record.enabled
    assert record.review_note == "Not relevant" and record.submitted_by == {
        "name": "First contributor"
    }
    assert len(session.added) == 1


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_enabled_cannot_bypass_approval_in_any_ingestion_entrypoint(status):
    record = source(status)
    with pytest.raises(ValueError):
        jobs.request_ingestion(session_with(), record)
    with pytest.raises(services.OperationConflict, match="Approve"):
        services.fetch_source(session_with(record), record.id)
    job = IngestionJob(
        id=uuid.uuid4(),
        source_id=record.id,
        status="queued",
        available_at=utcnow() - timedelta(seconds=1),
    )
    session = session_with(job)
    session.get = lambda *a: record
    with pytest.raises(services.OperationConflict, match="Approve"):
        services.prepare_immediate_dispatch(session, job.id)
    session = session_with(record, job)
    assert jobs.claim_job(session, job.id) is None
    assert job.status == "failed" and job.error == "Source is not approved for ingestion"
    assert record.last_attempt_at is None and record.consecutive_failures is None


@pytest.mark.parametrize("http_status", [200, 304])
def test_source_rejected_during_http_cannot_commit_articles(monkeypatch, rss_bytes, http_status):
    record = source("approved")
    job = IngestionJob(
        id=uuid.uuid4(), source_id=record.id, status="running", lease_token=uuid.uuid4(), attempts=1
    )

    @contextmanager
    def begin():
        # Lock source before checking job ownership, then re-read approval.
        yield session_with(record, job, record)

    monkeypatch.setattr(tasks, "session_factory", lambda: SimpleNamespace(begin=begin))
    monkeypatch.setattr(tasks, "claim_job", lambda *a: (job, record))
    from devfeed_core.feeds.fetcher import FetchResult

    def fetch(*a):
        record.approval_status = "rejected"
        return FetchResult(http_status, rss_bytes if http_status == 200 else b"", record.feed_url)

    monkeypatch.setattr(tasks, "fetch_feed", fetch)
    monkeypatch.setattr(tasks, "store_entries", lambda *a: pytest.fail("Stored rejected articles"))
    tasks.ingest(str(job.id))
    assert job.status == "failed" and record.last_success_at is None


def test_source_enrichment_claim_and_request_reject_rejected_sources():
    record = source("rejected")
    with pytest.raises(services.OperationConflict):
        source_enrichment.request_enrichment(session_with(record), record.id)
    job = SourceEnrichmentJob(
        id=uuid.uuid4(),
        source_id=record.id,
        status="queued",
        attempts=0,
        available_at=utcnow() - timedelta(seconds=1),
    )
    assert source_enrichment.claim_enrichment(session_with(record, job), job.id) is None
    assert job.status == "failed"


def test_enrichment_fetch_is_outside_transaction_and_does_not_change_review(monkeypatch):
    record = source("pending", submitted_by={"name": "Contributor"})
    job = SourceEnrichmentJob(
        id=uuid.uuid4(),
        source_id=record.id,
        status="running",
        lease_token=uuid.uuid4(),
        attempts=1,
        changed_fields=[],
    )
    events = []

    @contextmanager
    def begin():
        events.append("begin")
        yield session_with(record, job)
        events.append("commit")

    monkeypatch.setattr(source_tasks, "session_factory", lambda: SimpleNamespace(begin=begin))
    monkeypatch.setattr(source_tasks, "claim_enrichment", lambda *a: (job, record))

    def lookup(*a):
        assert events == ["begin", "commit"]
        return {"description": "Engineering news", "logo_url": "https://example.com/logo"}, None

    monkeypatch.setattr(source_tasks, "lookup_profile", lookup)
    source_tasks.enrich_source(str(job.id))
    assert events == ["begin", "commit", "begin", "commit"]
    assert job.status == "succeeded" and record.description == "Engineering news"
    assert record.approval_status == "pending" and record.submitted_by == {"name": "Contributor"}
    assert record.last_success_at is None and record.metadata_enriched_at


def test_scheduler_dispatches_source_profile_jobs_with_distinct_function():
    job = SourceEnrichmentJob(id=uuid.uuid4(), source_id=uuid.uuid4(), status="queued")
    calls = []

    @contextmanager
    def begin():
        yield session_with(job)

    queue = SimpleNamespace(enqueue=lambda *a, **kw: calls.append(a) or SimpleNamespace(id="rq-id"))
    assert (
        scheduler.dispatch_jobs(
            SimpleNamespace(begin=begin), queue, 1, utcnow(), kind="source-enrichment"
        )
        == 1
    )
    assert calls == [("devfeed_aggregator.source_tasks.enrich_source", str(job.id))]


@pytest.mark.parametrize(
    "attempt,retryable,retry_after,expected_delay",
    [
        (1, True, 0, 30),
        (2, True, 0, 60),
        (1, True, 120, 120),
        (3, True, 0, None),
        (1, False, 0, None),
    ],
)
def test_profile_retry_limits_delays_and_lease_cleanup(
    monkeypatch, attempt, retryable, retry_after, expected_delay
):
    now = utcnow()
    monkeypatch.setattr(source_enrichment, "utcnow", lambda: now)
    job = SourceEnrichmentJob(
        attempts=attempt,
        status="running",
        lease_token=uuid.uuid4(),
        lease_until=now,
        dispatched_at=now,
    )
    fail_or_retry(job, "x" * 1500, now, retryable=retryable, retry_after=retry_after)
    assert job.lease_token is None and job.lease_until is None and job.dispatched_at is None
    assert len(job.error) == 1000
    if expected_delay is None:
        assert job.status == "failed" and job.finished_at == now
    else:
        assert job.status == "queued" and job.finished_at is None
        assert job.available_at == now + timedelta(seconds=expected_delay)


def test_profile_lease_recovery_is_bounded_and_retries_interrupted_work():
    now = utcnow()
    job = SourceEnrichmentJob(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        attempts=1,
        status="running",
        lease_token=uuid.uuid4(),
        lease_until=now - timedelta(seconds=1),
    )
    statements = []

    def scalars(statement):
        statements.append(statement)
        return SimpleNamespace(all=lambda: [job])

    @contextmanager
    def begin():
        yield SimpleNamespace(scalars=scalars)

    assert (
        scheduler.recover_jobs(SimpleNamespace(begin=begin), 10, now, kind="source-enrichment") == 1
    )
    assert job.status == "queued" and job.lease_token is None
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "SKIP LOCKED" in sql and "LIMIT" in sql and "lease_until <" in sql


@pytest.mark.parametrize("unavailable", [False, True])
def test_profile_dispatch_commits_override_before_targeted_broker_call(monkeypatch, unavailable):
    from redis.exceptions import ConnectionError

    now, events = utcnow(), []
    record = source("approved")
    job = SourceEnrichmentJob(
        id=uuid.uuid4(),
        source_id=record.id,
        status="queued",
        attempts=1,
        created_at=utcnow(),
        available_at=utcnow() + timedelta(minutes=3),
        dispatched_at=utcnow(),
        error="Previous failure",
        changed_fields=[],
    )
    session = session_with(job)
    session.get = lambda model, _: job if model is SourceEnrichmentJob else record

    @contextmanager
    def begin():
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
    monkeypatch.setattr(source_enrichment, "utcnow", lambda: now)

    def publish(actual_factory, actual_queue, batch, when, *, job_id, kind):
        assert events == ["commit"]
        assert (actual_factory, actual_queue, batch, job_id, kind) == (
            factory,
            queue,
            1,
            job.id,
            "source-enrichment",
        )
        assert job.available_at == now and job.dispatched_at is None
        events.append("publish")
        if unavailable:
            raise ConnectionError("Unavailable")
        job.dispatched_at = now
        return 1

    monkeypatch.setattr(dispatch, "dispatch_jobs", publish)
    if unavailable:
        with pytest.raises(ConnectionError):
            dispatch.dispatch_now(job.id, kind="source-enrichment")
        assert job.available_at == now and job.dispatched_at is None
    else:
        result = dispatch.dispatch_now(job.id, kind="source-enrichment")
        assert result["id"] == str(job.id) and result["status"] == "queued"
    assert events == ["commit", "publish", "close"]
    assert job.attempts == 1 and job.error == "Previous failure"


@pytest.mark.parametrize(
    "args",
    [
        ["sources", "approve"],
        ["sources", "reject"],
        ["sources", "enrich"],
        ["sources", "review-history"],
    ],
)
def test_new_cli_help_needs_no_services(args, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEVFEED_DATABASE_URL")
    monkeypatch.delenv("DEVFEED_REDIS_URL")
    with pytest.raises(SystemExit) as result:
        run([*args, "--help"])
    assert result.value.code == 0
