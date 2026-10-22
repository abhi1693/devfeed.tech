"""Source review scanning and private receipts over explicit, preloaded read boundaries."""

import copy
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import source_lifecycle, source_notifications
from devfeed_core import source_relevance as relevance
from devfeed_core.models import SourceReview, UserAccount
from devfeed_core.notifications import notification_subscriber_id
from devfeed_core.schemas import SourceDecision
from test_source_lifecycle_commands_unit import derive
from test_source_readiness_unit import NOW, assessment, configuration, profile, source


def assessed(current, verdict):
    inspected = profile(
        current,
        created_at=NOW - timedelta(hours=2),
        available_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(minutes=90),
    )
    sample = [
        {
            "index": index,
            "title": f"Software engineering architecture practices {index}",
            "summary": "",
        }
        for index in range(3)
    ]
    inspected.result["sample"] = sample
    reviewed = assessment(
        current,
        inspected,
        created_at=NOW - timedelta(hours=1),
        available_at=NOW - timedelta(hours=1),
        finished_at=NOW - timedelta(minutes=30),
    )
    output = relevance.SourceRelevance(
        relevance=verdict,
        confidence=0.98,
        reason=(
            "The sampled entries establish the publication's software engineering focus."
            if verdict == "relevant"
            else "The supplied titles lack enough context to establish the editorial focus."
        ),
        entries=[
            {"index": row["index"], "relevance": verdict, "evidence": row["title"]}
            for row in sample
        ],
    )
    current.relevance_assessment.update(
        **output.model_dump(mode="json"),
        approval_supported=relevance.approval_supported(output, sample),
        rejection_supported=relevance.rejection_supported(output, sample),
    )
    reviewed.result["output"] = copy.deepcopy(current.relevance_assessment)
    return {"profile": inspected, "assessment": reviewed}


@pytest.mark.parametrize("disabled", ["ai", "automation", "zero-limit"])
def test_pending_scan_requires_both_automation_switches_and_a_positive_budget(
    monkeypatch, disabled
):
    settings = configuration(ai_enabled=disabled != "ai", full_automation=disabled != "automation")
    if disabled == "ai":
        # Full automation enables AI at startup; the independent kill switch
        # can subsequently pause it while the scheduler remains alive.
        settings.ai_enabled = False
    monkeypatch.setattr(relevance, "get_settings", lambda: settings)
    factory = Mock()
    assert (
        relevance.schedule_pending_reviews(factory, limit=0 if disabled == "zero-limit" else 2) == 0
    )
    factory.begin.assert_not_called()


@pytest.mark.parametrize(
    "mode", ["profile", "assessment", "supported", "manual", "uncertain", "lost-lock"]
)
def test_pending_scan_reconciles_only_actionable_or_supported_current_facts(monkeypatch, mode):
    settings = configuration(ai_enabled=True, full_automation=True)
    current = source(feed_url="https://publisher.example/feed")
    jobs = {}
    if mode == "assessment":
        jobs["profile"] = profile(current)
    elif mode in {"supported", "manual", "uncertain", "lost-lock"}:
        jobs = assessed(current, "uncertain" if mode == "uncertain" else "relevant")
    if mode == "manual":
        current.processing_request = {
            "id": str(uuid.uuid4()),
            "kind": "reopen_review",
            "actor": "editor",
            "requested_at": (NOW - timedelta(hours=3)).isoformat(),
            "manual_review": True,
            "required": ["profile", "branding"],
        }
    facts = derive(current, [], jobs, settings)
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    session.scalars.return_value.partitions.return_value = iter([[current]])
    session.scalar.return_value = None if mode == "lost-lock" else current
    monkeypatch.setattr(relevance, "get_settings", lambda: settings)
    monkeypatch.setattr(
        source_lifecycle, "source_readiness_batch", lambda _session, rows: {facts.key: facts}
    )
    reconcile = Mock()
    monkeypatch.setattr(source_lifecycle, "reconcile_source_processing", reconcile)
    expected = mode in {"profile", "assessment", "supported"}
    assert relevance.schedule_pending_reviews(factory) == int(expected)
    if expected:
        reconcile.assert_called_once_with(session, current, automatic=True)
    else:
        reconcile.assert_not_called()
    assert current.approval_status == "pending" and not current.enabled


def test_pending_scan_budget_is_shared_across_partitions_and_counts_only_claimed_rows(monkeypatch):
    settings = configuration(ai_enabled=True, full_automation=True)
    rows = [source(id=uuid.UUID(int=value)) for value in range(1, 5)]
    facts = {f"source:{row.id}": derive(row, [], {}, settings) for row in rows}
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    session.scalars.return_value.partitions.return_value = iter([rows[:2], rows[2:]])
    session.scalar.side_effect = [None, rows[1], rows[2]]
    monkeypatch.setattr(relevance, "get_settings", lambda: settings)
    monkeypatch.setattr(
        source_lifecycle,
        "source_readiness_batch",
        lambda _session, batch: {f"source:{row.id}": facts[f"source:{row.id}"] for row in batch},
    )
    reconcile = Mock()
    monkeypatch.setattr(source_lifecycle, "reconcile_source_processing", reconcile)
    assert relevance.schedule_pending_reviews(factory, limit=2) == 2
    assert [call.args[1] for call in reconcile.call_args_list] == rows[1:3]
    assert session.scalar.call_count == 3


@pytest.mark.parametrize(
    "case",
    [
        "disabled",
        "no-environment",
        "unverified",
        "malformed-user",
        "deleted-user",
        "approved",
        "rejected",
        "long-note",
    ],
)
def test_review_receipt_is_private_to_a_verified_existing_submitter(monkeypatch, case):
    user_id = uuid.uuid4()
    current = source(
        feed_url="https://publisher.example/feed",
        submitted_by={"verified": True, "user_id": str(user_id)},
    )
    current.name = "Engineering publication"
    decision = "approved" if case == "approved" else "rejected"
    current.approval_status = decision
    current.enabled = decision == "approved"
    decision_body = SourceDecision(
        decision=decision,
        actor="editor",
        note=(
            ("Detailed editorial reason. " * 40)[:1000]
            if case == "long-note"
            else "Unrelated publication focus"
        ),
    )
    current.reviewed_at, current.reviewed_by, current.review_note = (
        NOW,
        "editor",
        decision_body.note,
    )
    review = SourceReview(
        id=uuid.uuid4(),
        source_id=current.id,
        decision=decision,
        actor="editor",
        note=decision_body.note,
        created_at=NOW,
    )
    settings = configuration(
        notifications_enabled=case != "disabled",
        chimely_user_environment="" if case == "no-environment" else "reader-inbox",
    )
    if case == "unverified":
        current.submitted_by["verified"] = False
    elif case == "malformed-user":
        current.submitted_by["user_id"] = "not-a-uuid"
    user = UserAccount(
        id=user_id, issuer="https://identity.example", subject="reader", organization_id="team"
    )
    session, enqueue = Mock(), Mock()
    session.get.return_value = None if case == "deleted-user" else user
    monkeypatch.setattr(source_notifications, "get_settings", lambda: settings)
    monkeypatch.setattr(source_notifications, "enqueue_notification", enqueue)
    source_notifications.notify_source_review(session, current, review)
    if case not in {"approved", "rejected", "long-note"}:
        enqueue.assert_not_called()
        if case != "deleted-user":
            session.get.assert_not_called()
        return
    session.get.assert_called_once_with(UserAccount, user_id)
    args, kwargs = enqueue.call_args
    assert args[:2] == (session, f"source-review:{review.id}")
    message = args[2]
    assert kwargs == {
        "audience": "user",
        "subscriber_id": notification_subscriber_id(
            audience="user",
            issuer=user.issuer,
            subject=user.subject,
            organization_id=user.organization_id,
        ),
    }
    assert message.category == f"sources.{decision}"
    assert message.action_url == (
        f"/sources/{current.id}" if decision == "approved" else "/sources/suggest"
    )
    assert message.severity == ("success" if decision == "approved" else "warning")
    assert current.name in message.body and decision in message.body
    if case == "approved":
        assert "Reason:" not in message.body
    elif case == "long-note":
        assert len(message.body) == 1000 and message.body.endswith("…")
    else:
        assert review.note in message.body
    assert user.subject not in message.body and user.issuer not in message.body


def test_long_source_passages_offer_bounded_verbatim_evidence_without_changing_the_sample():
    passage = "Software product architecture " * 30
    entry = {"index": 0, "title": "Short", "summary": passage}
    options = relevance.evidence_options(entry)
    assert options == [" ".join(passage.split())[:500]]
    assert len(options[0]) == 500 and options[0] in passage
