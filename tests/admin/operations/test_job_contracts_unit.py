"""Operator job views expose reviewed fields and keep database use outside log transport."""

import uuid
from types import SimpleNamespace

import pytest
from devfeed_admin_api import jobs
from devfeed_admin_api.auth import AdminIdentity, actor
from devfeed_core.job_logs import JobLogEntry, JobLogPage
from devfeed_core.models import ArticleAnalysisJob, IngestionJob
from devfeed_core.services import OperationConflict
from devfeed_http.pagination import ListQuery
from fastapi import HTTPException
from redis.exceptions import ConnectionError as RedisConnectionError

from tests.support.source_readiness import NOW, job


def ingestion(*, status="failed", **changes):
    return job(
        IngestionJob,
        status=status,
        source_id=uuid.uuid4(),
        dispatched_at=NOW,
        http_status=503 if status == "failed" else None,
        entries_seen=12,
        articles_created=4,
        entries_skipped=8,
        **changes,
    )


def administrator():
    return AdminIdentity(
        subject="admin-id",
        issuer="https://issuer.example",
        organization_id="org-id",
        roles=["admin"],
        name=" Reviewer ",
        email="reviewer@example.test",
        expires_at=int(NOW.timestamp()) + 300,
        csrf_token="session-csrf-token",
    )


@pytest.mark.parametrize(
    "retryable,status", [(True, "failed"), (False, "retried"), (None, "failed")]
)
def test_failed_history_presentation_distinguishes_retry_eligibility_from_stored_status(
    retryable, status
):
    current = ingestion()
    result = jobs.job_view(current, "ingestion", retryable=retryable)
    assert result.status == status and result.retryable is bool(retryable)
    assert current.status == "failed"  # Displaying supersession does not rewrite immutable history.
    assert result.source_id == current.source_id and result.error == current.error
    assert result.details == {
        "dispatched_at": NOW.isoformat(),
        "http_status": 503,
        "entries_seen": 12,
        "articles_created": 4,
        "entries_skipped": 8,
    }


def test_analysis_job_view_omits_private_input_catalog_and_worker_lease_fields():
    current = job(
        ArticleAnalysisJob,
        status="running",
        article_id=uuid.uuid4(),
        dispatched_at=NOW,
        input_snapshot={"text": "private-source-text"},
        catalog_snapshot={"private": "catalog"},
        input_hash="a" * 64,
        catalog_hash="b" * 64,
        editorial_revision=2,
        prompt_version="test-version",
        result={},
        outcome=None,
        usage={"input_tokens": 9},
        duration_ms=42,
    )
    current.model = "research-model"
    result = jobs.job_view(current, "analysis")
    wire = result.model_dump_json()
    assert result.status == "running" and not result.retryable
    assert result.details["model"] == "research-model" and result.details["usage"] == {
        "input_tokens": 9
    }
    assert result.details["dispatched_at"] == NOW.isoformat()
    assert not {"input_snapshot", "catalog_snapshot", "lease_token", "lease_until"}.intersection(
        result.details
    )
    assert "private-source-text" not in wire and "private" not in wire
    assert str(current.lease_token) not in wire


@pytest.mark.parametrize("empty", [False, True])
def test_job_list_composes_only_the_selected_page_with_subject_names_and_retry_state(
    monkeypatch, empty
):
    current = ingestion()
    query = ListQuery(q="", sort=None, limit=2 if empty else 1, offset=14 if empty else 4)
    page = {
        "items": [] if empty else [current],
        "total": 12,
        "limit": query.limit,
        "offset": query.offset,
    }
    received, closed = [], []
    monkeypatch.setattr(jobs, "paginate", lambda *args: received.append(args) or page)
    session = SimpleNamespace(
        execute=lambda _: SimpleNamespace(all=lambda: [(current.id, False, "Engineering feed")]),
        close=lambda: closed.append(True),
    )
    if empty:
        session.execute = lambda _: pytest.fail("An empty page does not need a metadata read")

    result = jobs.jobs("ingestion", session, query)

    assert result["total"] == 12
    assert result["offset"] == query.offset and result["limit"] == query.limit
    assert len(received) == 1 and received[0][2] is query
    assert received[0][4] == "-created_at"
    if empty:
        assert result["items"] == []
    else:
        assert result["items"][0].target_name == "Engineering feed"
        assert result["items"][0].id == current.id and result["items"][0].status == "retried"
    assert closed == [True]


@pytest.mark.parametrize("operation", ["list", "detail", "retry"])
def test_log_only_kind_cannot_be_used_to_bypass_job_action_visibility(operation):
    with pytest.raises(HTTPException, match="Job not found") as error:
        if operation == "list":
            jobs.jobs("source-discovery", object(), object())
        elif operation == "detail":
            jobs.detail("source-discovery", uuid.uuid4(), object())
        else:
            jobs.retry("source-discovery", uuid.uuid4(), object(), object())
    assert error.value.status_code == 404


def test_job_detail_combines_the_record_with_current_retry_metadata(monkeypatch):
    current, session = ingestion(), object()
    seen = []
    monkeypatch.setattr(jobs, "record", lambda *args: seen.append(args) or current)
    session = SimpleNamespace(
        execute=lambda _: SimpleNamespace(one=lambda: (current.id, True, "Publisher"))
    )
    result = jobs.detail("ingestion", current.id, session)
    assert seen == [(session, IngestionJob, current.id)]
    assert result.id == current.id and result.retryable and result.target_name == "Publisher"


def test_retry_passes_authenticated_attribution_and_returns_new_run_without_rewriting_history(
    monkeypatch,
):
    previous, replacement = ingestion(), ingestion(status="queued")
    replacement.source_id = previous.source_id
    completed, requested = [], []
    session = SimpleNamespace(commit=lambda: completed.append(True))
    identity = administrator()
    monkeypatch.setattr(jobs, "record", lambda *_: previous)
    monkeypatch.setattr(
        jobs, "retry_failed_job", lambda *args: requested.append(args) or replacement
    )

    result = jobs.retry("ingestion", previous.id, identity, session)

    assert requested == [(session, previous, "ingestion", actor(identity))]
    assert completed == [True]
    assert result.id == replacement.id and result.status == "queued"
    assert previous.status == "failed" and previous.id != result.id


def test_retry_conflicts_do_not_turn_into_success_or_commit(monkeypatch):
    previous = ingestion()
    monkeypatch.setattr(jobs, "record", lambda *_: previous)

    def changed(*args):
        raise OperationConflict("A newer run exists")

    monkeypatch.setattr(jobs, "retry_failed_job", changed)
    with pytest.raises(OperationConflict, match="newer run"):
        jobs.retry("ingestion", previous.id, administrator(), object())


@pytest.mark.parametrize("cursor", ["wrong", "-1-0", "18446744073709551616-0"])
def test_invalid_log_cursor_is_rejected_before_loading_a_job(monkeypatch, cursor):
    monkeypatch.setattr(jobs, "record", lambda *_: pytest.fail("Invalid cursor reached DB"))
    with pytest.raises(HTTPException, match="Invalid log cursor") as error:
        jobs.runtime_logs("ingestion", uuid.uuid4(), object(), object(), after=cursor)
    assert error.value.status_code == 422


def test_logs_release_database_before_transport_and_preserve_page_diagnostics(monkeypatch):
    current, events, redis = ingestion(status="running"), [], object()
    session = SimpleNamespace(close=lambda: events.append("closed"))
    monkeypatch.setattr(jobs, "record", lambda *_: current)
    page = JobLogPage(
        items=[
            JobLogEntry(
                id="12-1",
                timestamp=NOW,
                level="WARNING",
                message="Retrying origin",
                fields={"attempt": 2},
            )
        ],
        next_cursor="12-2",
        has_more=True,
        truncated=True,
        unreadable_entries=1,
        retention_seconds=3600,
        max_entries=50,
    )

    def read(client, kind, identifier, **options):
        assert events == ["closed"]
        assert client is redis and kind == "ingestion" and identifier == current.id
        assert options == {"after": "12-0", "limit": 2}
        return page

    monkeypatch.setattr(jobs, "read_job_logs", read)

    result = jobs.runtime_logs("ingestion", current.id, session, redis, after="12-0", limit=2)

    assert result.job_status == "running" and result.attempts == 1
    assert result.model_dump(exclude={"job_status", "attempts"}) == page.model_dump()


@pytest.mark.parametrize("failure", [RedisConnectionError, OSError, ValueError])
def test_log_outages_return_retryable_error_without_leaking_transport_details(monkeypatch, failure):
    current, closed = ingestion(), []
    session = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(jobs, "record", lambda *_: current)

    def unavailable(*args, **kwargs):
        assert closed == [True]
        raise failure("redis://user:private-password@internal-host")

    monkeypatch.setattr(jobs, "read_job_logs", unavailable)
    with pytest.raises(HTTPException, match="temporarily unavailable") as error:
        jobs.runtime_logs("ingestion", current.id, session, object())
    assert error.value.status_code == 503
    assert "private-password" not in str(error.value) and error.value.__suppress_context__
