"""Correction admission preserves current inputs and bounded independent review."""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import topic_decision_budget, topic_verification
from devfeed_core import topic_remediation as remediation
from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import TopicAnalysisJob
from taxonomy_command_helpers import (
    ACTOR,
    NOW,
    metadata_job,
    proposal,
    relationship_job,
    relationship_proposal,
    topic,
)
from test_source_readiness_unit import configuration
from test_taxonomy_automatic_review_unit import identity_check


def correction_attempt(current, *, attempt=0, error="codex_timeout"):
    document = {
        "topic": copy.deepcopy(current.proposed),
        "evidence": [],
        "correction": {
            "proposal_id": str(current.id),
            "previous_job_id": str(uuid.uuid4()),
            "attempt": attempt,
            "feedback": {"research_reasons": [], "topic_verification": {}, "failed_citations": []},
        },
    }
    return TopicAnalysisJob(
        id=uuid.uuid4(),
        proposal_id=current.id,
        input_snapshot=document,
        input_hash=snapshot_hash(current.proposed),
        prompt_version=remediation.PROMPT_VERSION,
        requested_by=remediation.ACTOR,
        status="failed",
        attempts=3,
        error=error,
        result={},
        usage={},
        created_at=NOW - timedelta(minutes=20),
        available_at=NOW - timedelta(minutes=19),
        finished_at=NOW - timedelta(minutes=10),
    )


@pytest.fixture
def runtime(monkeypatch):
    current = proposal(research_requested=True)
    previous = correction_attempt(current)
    value = SimpleNamespace(
        proposal=current,
        previous=previous,
        session=MagicMock(),
        factory=MagicMock(),
        settings=configuration(
            ai_enabled=True, full_automation=True, ai_bounded_topics_enabled=False
        ),
        locked=True,
        pending=0,
        candidates=[previous],
        fresh=[],
        proposals={current.id: current},
    )
    value.factory.begin.return_value.__enter__.return_value = value.session
    value.lock, value.review = Mock(), Mock()
    monkeypatch.setattr(remediation, "lock_topics", value.lock)
    monkeypatch.setattr(remediation, "utcnow", lambda: NOW)
    monkeypatch.setattr(remediation, "get_settings", lambda: value.settings)

    def review(_session, identifier, body, actor):
        row = value.proposals[identifier]
        assert actor == remediation.ACTOR and body.expected_input_hash == snapshot_hash(
            row.proposed
        )
        row.status, row.reviewed_at, row.reviewed_by, row.review_note = (
            body.decision,
            NOW,
            actor,
            body.note,
        )
        return row

    value.review.side_effect = review
    monkeypatch.setattr(remediation, "review_proposal", value.review)
    value.session.get.side_effect = lambda _model, identifier, **_: value.proposals.get(identifier)
    value.created = []

    def add(row):
        row.id = uuid.uuid4()
        value.created.append(row)

    value.session.add.side_effect = add
    return value


def run(value):
    value.session.scalar.side_effect = [value.locked, value.pending]
    value.session.scalars.side_effect = [
        SimpleNamespace(all=lambda: value.candidates),
        SimpleNamespace(all=lambda: value.fresh),
    ]
    return remediation.schedule_topic_corrections(value.factory)


def test_retry_binds_current_input_previous_run_and_attempt_without_approving(runtime):
    assert run(runtime) == 1
    queued = runtime.created[0]
    assert queued.proposal_id == runtime.proposal.id and queued.input_hash == snapshot_hash(
        runtime.proposal.proposed
    )
    receipt = queued.input_snapshot["correction"]
    assert receipt["previous_job_id"] == str(runtime.previous.id) and receipt["attempt"] == 1
    assert receipt["proposal_id"] == str(runtime.proposal.id)
    assert runtime.previous.result["correction_status"] == "queued"
    assert runtime.previous.result["correction_job_id"] == str(queued.id)
    assert runtime.proposal.status == "pending" and not runtime.proposal.research_requested
    runtime.review.assert_not_called()


def test_new_human_input_starts_fresh_without_inheriting_an_old_adverse_verdict(runtime):
    runtime.previous = metadata_job(runtime.proposal)
    runtime.candidates = [runtime.previous]
    original_input = copy.deepcopy(runtime.previous.input_snapshot)
    check = identity_check(runtime.proposal)["check"]
    check["verdict"] = "unsupported"
    check["relevance"].update(
        verdict="out_of_scope", reason="An earlier independent scope assessment."
    )
    runtime.previous.result["topic_verification"] = topic_verification.checked_verdict(
        check, topic_verification.verification_input(runtime.proposal)
    )
    runtime.proposal.proposed = {
        **runtime.proposal.proposed,
        "description": "A clarified developer-library identity.",
    }
    assert run(runtime) == 1
    queued = runtime.created[0]
    assert queued.input_hash == snapshot_hash(runtime.proposal.proposed)
    assert queued.input_snapshot["correction"]["attempt"] == 0
    assert queued.input_snapshot["correction"]["feedback"] == {
        "research_reasons": [],
        "topic_verification": {},
        "failed_citations": [],
    }
    assert runtime.previous.input_snapshot == original_input
    assert runtime.proposal.status == "pending"
    runtime.review.assert_not_called()


@pytest.mark.parametrize(
    "reason", ["attempt-budget", "out-of-scope", "unverified-identity", "terminal-error"]
)
def test_terminal_current_failures_receive_attributed_rejection_instead_of_an_unbounded_retry(
    runtime, reason
):
    if reason == "attempt-budget":
        runtime.previous = correction_attempt(
            runtime.proposal, attempt=runtime.settings.topic_correction_max_attempts
        )
    elif reason == "terminal-error":
        runtime.previous = correction_attempt(
            runtime.proposal, error=next(iter(remediation.TERMINAL_ANALYSIS_ERRORS))
        )
    else:
        runtime.previous = metadata_job(runtime.proposal)
        check = identity_check(runtime.proposal)["check"]
        check["verdict"] = "unsupported"
        if reason == "out-of-scope":
            check["relevance"].update(
                verdict="out_of_scope",
                reason="This exact identity is outside the developer taxonomy.",
            )
        else:
            next(field for field in check["fields"] if field["field"] == "name")["supported"] = (
                False
            )
        runtime.previous.result["topic_verification"] = topic_verification.checked_verdict(
            check, topic_verification.verification_input(runtime.proposal)
        )
    runtime.candidates = [runtime.previous]
    assert run(runtime) == 0
    assert (
        runtime.proposal.status == "rejected" and runtime.proposal.reviewed_by == remediation.ACTOR
    )
    assert runtime.proposal.reviewed_at == NOW
    assert runtime.previous.result["correction_status"] == "rejected"
    audit = runtime.previous.result["automatic_rejection"]
    assert (
        audit["proposal_id"] == str(runtime.proposal.id)
        and audit["reason"] == runtime.proposal.review_note
    )
    assert runtime.created == []


@pytest.mark.parametrize("changed", ["deleted", "reviewed"])
def test_a_proposal_reviewed_or_removed_after_candidate_selection_is_left_alone(runtime, changed):
    if changed == "deleted":
        runtime.proposals.clear()
    else:
        runtime.proposal.status, runtime.proposal.reviewed_at, runtime.proposal.reviewed_by = (
            "rejected",
            NOW,
            ACTOR,
        )
    assert run(runtime) == 0
    assert runtime.created == [] and runtime.previous.result == {}
    runtime.review.assert_not_called()


def test_one_remaining_slot_is_not_spent_twice_when_multiple_candidates_were_selected(runtime):
    runtime.pending = remediation.MAX_PENDING - 1
    second = proposal(
        slug="python", proposed={**runtime.proposal.proposed, "name": "Python", "slug": "python"}
    )
    later = correction_attempt(second)
    later.finished_at = NOW - timedelta(minutes=9)
    runtime.proposals[second.id] = second
    runtime.candidates.append(later)
    assert run(runtime) == 1
    assert len(runtime.created) == 1 and runtime.created[0].proposal_id == runtime.proposal.id
    assert later.result == {} and second.research_requested is False


def test_complete_untouched_import_also_enters_independent_correction_review(runtime):
    fresh = proposal(research_requested=True)
    fresh.proposed.update(
        description="React is a JavaScript library for building user interfaces.",
        aliases=["ReactJS"],
        keywords=["react-components"],
        website_url="https://react.dev/",
        logo_url="https://react.dev/logo.png",
        facts=[
            {
                "name": "Documentation",
                "value": "Official component reference",
                "source_url": "https://react.dev/learn",
                "retrieved_at": NOW.isoformat(),
            }
        ],
    )
    runtime.proposal, runtime.previous = fresh, None
    runtime.proposals = {fresh.id: fresh}
    runtime.candidates, runtime.fresh = [], [fresh]
    assert run(runtime) == 1
    queued = runtime.created[0]
    assert queued.input_snapshot["correction"]["previous_job_id"] is None
    assert queued.input_snapshot["correction"]["attempt"] == 0
    assert runtime.proposal.status == "pending"
    runtime.review.assert_not_called()


@pytest.mark.parametrize("guard", ["disabled", "lock-denied", "full"])
def test_correction_admission_respects_manual_mode_and_current_capacity(runtime, guard):
    if guard == "disabled":
        runtime.settings = configuration(full_automation=False, ai_bounded_topics_enabled=False)
    elif guard == "lock-denied":
        runtime.locked = False
    else:
        runtime.pending = remediation.MAX_PENDING
    assert run(runtime) == 0
    assert runtime.created == []
    runtime.review.assert_not_called()


def test_bounded_mode_uses_the_durable_decision_budget_scheduler(runtime, monkeypatch):
    runtime.settings = configuration(
        ai_enabled=True, full_automation=True, ai_bounded_topics_enabled=True
    )
    schedule = Mock(return_value=2)
    monkeypatch.setattr(topic_decision_budget, "schedule_decisions", schedule)
    assert run(runtime) == 2
    schedule.assert_called_once_with(runtime.factory)
    runtime.factory.begin.assert_not_called()


@pytest.mark.parametrize("mode", ["manual", "empty", "failed-verification"])
def test_relationship_finalization_uses_attributed_normal_review_without_erasing_other_rejections(
    runtime, monkeypatch, mode
):
    focal, peer = topic(kind="library"), topic("JavaScript", kind="language")
    job = relationship_job(focal, peer)
    row = relationship_proposal(focal, peer, job)
    previous = {
        "proposal_id": str(uuid.uuid4()),
        "reason": "Earlier independent verification failed",
    }
    job.result["automatic_rejections"] = [previous]
    if mode == "manual":
        runtime.settings = configuration(full_automation=False)
    jobs = [] if mode == "empty" else [job]
    runtime.session.scalars.side_effect = [
        SimpleNamespace(all=lambda: jobs),
        SimpleNamespace(all=lambda: [row]),
    ]

    def review(_session, identifier, body, actor):
        assert identifier == row.id and body.expected_input_hash == remediation.proposal_hash(row)
        row.status, row.reviewed_at, row.reviewed_by, row.review_note = (
            body.decision,
            NOW,
            actor,
            body.note,
        )

    reviewer = Mock(side_effect=review)
    monkeypatch.setattr(remediation, "review_relationship", reviewer)
    assert remediation.finalize_relationship_reviews(runtime.factory) == (
        1 if mode == "failed-verification" else 0
    )
    if mode == "failed-verification":
        assert row.status == "rejected" and row.reviewed_by == remediation.ACTOR
        assert job.result["automatic_rejections"] == [
            previous,
            {"proposal_id": str(row.id), "reason": row.review_note},
        ]
    else:
        reviewer.assert_not_called()
