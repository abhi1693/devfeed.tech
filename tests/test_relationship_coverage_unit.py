"""Relationship coverage cursor safety, bounded retries, and scheduling admission."""

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import relationship_coverage as coverage
from devfeed_core import topic_decision_budget
from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import TopicRelationshipScan
from devfeed_core.services import OperationConflict

NOW = datetime(2026, 10, 4, tzinfo=UTC)
ID, THROUGH = uuid.UUID(int=1), uuid.UUID(int=2)


def scan(**changes):
    return TopicRelationshipScan(
        **{
            "topic_id": ID,
            "generation": 7,
            "topic_snapshot": {"name": "Python"},
            "failures": 0,
            "job_id": uuid.UUID(int=3),
            **changes,
        }
    )


@pytest.mark.parametrize("failures,hours", [(0, 1), (1, 2), (4, 16), (5, 24), (20, 24)])
def test_failed_batches_back_off_without_advancing_the_cursor(failures, hours):
    value = scan(failures=failures, after_topic_id=ID)
    coverage._completed_job(value, SimpleNamespace(status="failed", error="quota"), NOW)
    assert value.failures == failures + 1 and value.last_error == "quota"
    assert value.next_run_at == NOW + timedelta(hours=hours)
    assert value.after_topic_id == ID and value.job_id is None


@pytest.mark.parametrize(
    "outcome,stale,full,proposals,advance",
    [
        ("cancelled", False, False, True, False),
        ("enriched", True, False, True, False),
        ("enriched", False, True, True, False),
        ("enriched", False, True, False, True),
        ("no_additions", False, False, False, True),
    ],
)
def test_only_current_complete_batches_advance_the_cursor(outcome, stale, full, proposals, advance):
    value = scan(failures=4, last_error="previous failure", after_topic_id=ID)
    job = SimpleNamespace(
        status="succeeded",
        outcome=outcome,
        input_snapshot={
            "topic": {"name": "Old"} if stale else value.topic_snapshot,
            "coverage": {"through_topic_id": str(THROUGH)},
        },
        result={
            "relationships": list(range(20 if full else 2)),
            "proposal_ids": ["proposal"] if proposals else [],
        },
    )
    coverage._completed_job(value, job, NOW)
    assert value.after_topic_id == (THROUGH if advance else ID)
    assert value.job_id is None and value.next_run_at == NOW
    assert value.failures == (4 if stale or outcome == "cancelled" else 0)


@pytest.mark.parametrize("existing", [False, True])
def test_identity_change_resets_progress_and_allocates_a_new_generation(monkeypatch, existing):
    session, topic = Mock(), SimpleNamespace(id=ID)
    session.scalar.return_value = 42
    monkeypatch.setattr(coverage, "topic_snapshot", lambda _: {"name": "New"})
    monkeypatch.setattr(coverage, "utcnow", lambda: NOW)
    value = coverage.reset_scan(
        session, topic, scan(failures=5, after_topic_id=THROUGH) if existing else None
    )
    assert value.generation == 42 and value.topic_snapshot == {"name": "New"}
    assert value.after_topic_id is value.job_id is value.finished_at is value.last_error is None
    assert value.failures == 0 and value.next_run_at == NOW
    assert session.add.call_count == (0 if existing else 1)


def test_oversized_batches_shrink_until_the_model_can_accept_them(monkeypatch):
    request = Mock(
        side_effect=[
            OperationConflict("keep within the model limit"),
            OperationConflict("within the model limit"),
            "job",
        ]
    )
    monkeypatch.setattr(coverage, "request_relationship_analysis", request)
    peers = [uuid.UUID(int=i) for i in range(1, 6)]
    assert coverage._request_batch(Mock(), SimpleNamespace(id=ID), peers) == ("job", peers[0])
    assert [call.kwargs["candidate_ids"] for call in request.call_args_list] == [
        peers,
        peers[:2],
        peers[:1],
    ]


@pytest.mark.parametrize(
    "peers,message", [([ID], "within the model limit"), ([ID, THROUGH], "active job")]
)
def test_unrecoverable_admission_conflicts_propagate(monkeypatch, peers, message):
    monkeypatch.setattr(
        coverage, "request_relationship_analysis", Mock(side_effect=OperationConflict(message))
    )
    with pytest.raises(OperationConflict, match=message):
        coverage._request_batch(Mock(), SimpleNamespace(id=ID), peers)


@pytest.mark.parametrize(
    "ai,research,allowance", [(False, True, True), (True, False, True), (True, True, False)]
)
def test_disabled_or_exhausted_automation_does_not_schedule(monkeypatch, ai, research, allowance):
    session, factory = Mock(), Mock()
    factory.begin.return_value = nullcontext(session)
    monkeypatch.setattr(
        coverage,
        "get_settings",
        lambda: SimpleNamespace(ai_enabled=ai, auto_research_relationships=research),
    )
    monkeypatch.setattr(topic_decision_budget, "relationship_allowance", lambda _: allowance)
    assert coverage.schedule_relationship_coverage(factory) == {
        "relationship_jobs_scheduled": 0,
        "relationship_scans_completed": 0,
    }
    session.scalars.assert_not_called()


@pytest.mark.parametrize(
    "state",
    [
        "inactive",
        "complete",
        "no-slots",
        "conflict",
        "scheduled",
        "new-scan",
        "completed-batch",
        "no-cursor",
    ],
)
def test_scheduler_preserves_cursor_and_only_counts_admitted_work(monkeypatch, state):
    session, factory = Mock(), Mock()
    factory.begin.return_value = nullcontext(session)
    value = scan(job_id=None, after_topic_id=THROUGH)
    if state == "no-cursor":
        value.after_topic_id = None
    topic = SimpleNamespace(id=ID, status="inactive" if state == "inactive" else "active")
    job = SimpleNamespace(id=uuid.UUID(int=9), input_snapshot={"topic": value.topic_snapshot})
    settings = SimpleNamespace(
        ai_enabled=True,
        auto_research_relationships=True,
        automation_batch_size=10,
        relationship_research_max_pending=2,
        relationship_research_batch_size=5,
    )
    monkeypatch.setattr(coverage, "get_settings", lambda: settings)
    monkeypatch.setattr(coverage, "utcnow", lambda: NOW)
    monkeypatch.setattr(topic_decision_budget, "relationship_allowance", lambda _: True)
    monkeypatch.setattr(coverage, "lock_topics", Mock())
    reset = Mock()
    monkeypatch.setattr(coverage, "reset_scan", reset)
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [topic] if state == "new-scan" else []),
        SimpleNamespace(all=lambda: [value]),
        [] if state == "complete" else [ID],
    ]
    completed_job = SimpleNamespace(
        status="succeeded",
        outcome="no_additions",
        input_snapshot={
            "topic": value.topic_snapshot,
            "coverage": {"through_topic_id": str(THROUGH)},
        },
        result={},
    )
    session.execute.return_value.all.return_value = (
        [(value, completed_job)] if state == "completed-batch" else []
    )
    session.scalar.return_value = 2 if state == "no-slots" else 0
    session.get.return_value = topic
    request = (
        Mock(side_effect=OperationConflict("quota"))
        if state == "conflict"
        else Mock(return_value=(job, ID))
    )
    monkeypatch.setattr(coverage, "_request_batch", request)
    result = coverage.schedule_relationship_coverage(factory)
    assert result == {
        "relationship_jobs_scheduled": int(
            state in {"scheduled", "new-scan", "completed-batch", "no-cursor"}
        ),
        "relationship_scans_completed": int(state == "complete"),
    }
    assert value.after_topic_id == (None if state == "no-cursor" else THROUGH)
    assert reset.call_count == int(state == "new-scan")
    if state in {"scheduled", "new-scan", "completed-batch", "no-cursor"}:
        assert value.job_id == job.id
        assert job.input_snapshot["coverage"] == {"generation": 7, "through_topic_id": str(ID)}
        assert job.input_hash == snapshot_hash(job.input_snapshot)
    elif state == "conflict":
        assert value.failures == 1 and value.next_run_at == NOW + timedelta(hours=1)
    else:
        request.assert_not_called()
