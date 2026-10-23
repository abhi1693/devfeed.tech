"""Discovery review delegates editorial authority and preserves candidate ownership."""

import copy
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import discovery, source_enrichment
from devfeed_core.models import CandidateAssessment, CandidateFeed, Source, SourceCandidate
from devfeed_core.services import OperationConflict, RecordNotFound

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def pending_candidate(**changes):
    return SourceCandidate(
        **{
            "id": uuid.uuid4(),
            "name": "Engineering publisher",
            "identity_url": "https://publisher.example/",
            "status": "pending",
            "approval_status": "pending",
            "review": {},
            "created_at": NOW - timedelta(days=2),
            "updated_at": NOW - timedelta(days=1),
            **changes,
        }
    )


def pending_source(row, **changes):
    return Source(
        **{
            "id": row.id,
            "name": row.name,
            "slug": "engineering-publisher",
            "website_url": row.identity_url,
            "feed_url": None,
            "source_type": "publisher",
            "approval_status": "pending",
            "enabled": False,
            "submission_channel": "api",
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "processing_request": {},
            **changes,
        }
    )


@pytest.fixture
def review(monkeypatch):
    row = pending_candidate()
    source = pending_source(row)
    row.source_id = source.id
    feed = CandidateFeed(
        id=uuid.uuid4(),
        candidate_id=row.id,
        url="https://publisher.example/feed",
        evidence={},
        checked_at=NOW,
    )
    session = Mock()
    session.get.side_effect = lambda model, key: (
        source
        if model is Source and key == source.id
        else feed
        if model is CandidateFeed and key == feed.id
        else None
    )
    factory = Mock(return_value=nullcontext(session))
    factory.begin.side_effect = lambda: nullcontext(session)
    monkeypatch.setattr(discovery, "session_factory", lambda: factory)
    monkeypatch.setattr(discovery, "candidate", Mock(return_value=row))
    idle, enqueue = Mock(), Mock()
    monkeypatch.setattr(discovery, "require_idle", idle)
    monkeypatch.setattr(discovery, "enqueue", enqueue)
    monkeypatch.setattr(discovery, "utcnow", lambda: NOW)
    return SimpleNamespace(
        row=row, source=source, feed=feed, session=session, idle=idle, enqueue=enqueue
    )


def reviewed_source(value, status):
    value.row.status, value.row.selected_feed_id = status, value.feed.id
    if status == "linked":
        value.source.id = uuid.uuid4()  # A duplicate links to the canonical source's own identity.
        value.row.source_id = value.source.id
    value.source.feed_url = value.feed.url
    value.source.approval_status = "rejected" if status == "rejected" else "approved"
    value.source.enabled = status != "rejected"
    value.source.reviewed_at, value.source.reviewed_by = NOW - timedelta(days=1), "previous editor"
    value.source.review_note = "Reviewed publisher coverage."
    if status != "linked":
        value.row.approval_status = value.source.approval_status


def test_attaching_import_placeholder_creates_disabled_pending_source_without_editorial_decision():
    row, session = pending_candidate(), Mock()
    result = discovery.attach_source(session, row)
    assert result.id == row.id == row.source_id
    assert result.name == row.name and result.website_url == row.identity_url
    assert result.approval_status == "pending" and not result.enabled
    assert result.feed_url is None and result.source_type == "publisher"
    assert result.submission_channel == "api"
    session.add.assert_called_once_with(result)
    session.flush.assert_called_once_with()


def test_existing_source_attachment_uses_saved_identity_without_creating_replacement(review):
    assert discovery.attach_source(review.session, review.row) is review.source
    review.session.add.assert_not_called()


@pytest.mark.parametrize("status", ["linked", "admitted"])
def test_linked_candidate_cannot_restart_discovery_or_reopen_source(review, status):
    reviewed_source(review, status)
    before = copy.deepcopy(review.source.approval_status)
    with pytest.raises(OperationConflict, match="already linked"):
        discovery.request(review.row.id, reopen=True, actor="editor", reason="Try again")
    assert review.source.approval_status == before
    review.enqueue.assert_not_called()


@pytest.mark.parametrize("missing", ["flag", "actor", "reason"])
def test_rejected_candidate_requires_explicit_attributed_reopening(review, missing):
    review.row.status = review.row.approval_status = "rejected"
    review.source.approval_status = "rejected"
    values = {"reopen": True, "actor": "editor", "reason": "New engineering coverage"}
    values[{"flag": "reopen", "actor": "actor", "reason": "reason"}[missing]] = (
        False if missing == "flag" else None
    )
    with pytest.raises(OperationConflict, match="Reopening rejection requires"):
        discovery.request(review.row.id, **values)
    assert review.source.approval_status == "rejected"
    review.enqueue.assert_not_called()


def test_reopening_records_audit_and_queues_discovery_without_granting_approval(review):
    review.row.status = review.row.approval_status = "rejected"
    review.source.approval_status = "rejected"
    review.source.reviewed_at, review.source.reviewed_by = (
        NOW - timedelta(days=1),
        "previous editor",
    )
    result = discovery.request(
        review.row.id,
        reopen=True,
        actor="editor",
        reason="Publisher added engineering articles",
        background=True,
    )
    assert result == {"candidate_id": review.row.id, "stage": "discover", "queued": True}
    assert (
        review.row.status
        == review.row.approval_status
        == review.source.approval_status
        == "pending"
    )
    assert not review.source.enabled and review.source.reviewed_by == "previous editor"
    assert review.row.review == {
        "decision": "reopened",
        "actor": "editor",
        "reason": "Publisher added engineering articles",
        "at": NOW.isoformat(),
    }
    audit = review.session.add.call_args.args[0]
    assert isinstance(audit, CandidateAssessment)
    assert audit.candidate_id == review.row.id and audit.evidence == review.row.review
    review.enqueue.assert_called_once_with(
        review.session, review.row.id, "discover", background=True
    )


@pytest.mark.parametrize(
    "actor,reason",
    [
        (None, "Reviewed"),
        (" ", "Reviewed"),
        ("x" * 201, "Reviewed"),
        ("editor", None),
        ("editor", " "),
        ("editor", "x" * 1001),
    ],
)
def test_review_audit_rejects_missing_or_unbounded_attribution_before_mutation(actor, reason):
    row, session = pending_candidate(), Mock()
    before = copy.deepcopy(row.review)
    with pytest.raises(ValueError, match="Review requires an actor"):
        discovery.audit(session, row, "reopened", actor, reason)
    assert row.review == before
    session.add.assert_not_called()


@pytest.mark.parametrize("has_job", [False, True])
def test_assessment_request_delegates_to_common_source_review_instead_of_legacy_ai_lane(
    review, monkeypatch, has_job
):
    review.row.status, review.row.selected_feed_id = "ready", review.feed.id
    review.source.feed_url = review.feed.url
    job = SimpleNamespace(id=uuid.uuid4()) if has_job else None
    request = Mock(return_value=job)
    monkeypatch.setattr(source_enrichment, "request_source_review", request)
    result = discovery.request(review.row.id, "assess")
    assert result == {
        "candidate_id": review.row.id,
        "stage": "source-review",
        "queued": True,
        "job_id": job.id if job else None,
    }
    request.assert_called_once_with(review.session, review.source)
    review.enqueue.assert_not_called()
    assert review.source.approval_status == "pending" and not review.source.enabled


def test_assessment_requires_selected_feed_before_requesting_source_review(review):
    with pytest.raises(OperationConflict, match="Select a discovered feed"):
        discovery.request(review.row.id, "assess")
    review.enqueue.assert_not_called()


@pytest.mark.parametrize("invalid", ["missing", "foreign", "rejected", "admitted", "linked"])
def test_feed_selection_rejects_foreign_or_reviewed_candidate_without_handoff(
    review, monkeypatch, invalid
):
    handoff = Mock()
    monkeypatch.setattr(discovery, "handoff", handoff)
    feed_id = review.feed.id
    if invalid == "missing":
        feed_id = uuid.uuid4()
    elif invalid == "foreign":
        review.feed.candidate_id = uuid.uuid4()
    else:
        reviewed_source(review, invalid)
    previous_selection = review.row.selected_feed_id
    with pytest.raises(RecordNotFound if invalid in {"missing", "foreign"} else OperationConflict):
        discovery.select_feed(review.row.id, feed_id)
    assert review.row.selected_feed_id == previous_selection
    handoff.assert_not_called()


def test_feed_selection_saves_choice_and_hands_off_to_pending_source_review(review, monkeypatch):
    handoff = Mock()
    monkeypatch.setattr(discovery, "handoff", handoff)
    assert discovery.select_feed(review.row.id, review.feed.id) == {
        "candidate_id": review.row.id,
        "selected_feed_id": review.feed.id,
    }
    assert review.row.selected_feed_id == review.feed.id and review.row.status == "ready"
    assert review.row.updated_at == NOW
    handoff.assert_called_once_with(review.session, review.row)


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_linked_candidate_review_never_changes_canonical_source(review, monkeypatch, decision):
    reviewed_source(review, "linked")
    boundary = Mock()
    monkeypatch.setattr(discovery, "review_source", boundary)
    result = getattr(discovery, decision)(review.row.id, "editor", "Reviewed import provenance")
    assert result["status"] == "linked"
    assert review.source.approval_status == "approved" and review.source.enabled
    boundary.assert_not_called()
    if decision == "reject":
        assert review.row.review["decision"] == "rejected"


@pytest.mark.parametrize("decision,expected", [("approve", "approved"), ("reject", "rejected")])
def test_compatibility_review_delegates_attributed_decision_to_source_service(
    review, monkeypatch, decision, expected
):
    review.row.status, review.row.selected_feed_id = "ready", review.feed.id
    review.source.feed_url = review.feed.url
    boundary = Mock()
    monkeypatch.setattr(discovery, "review_source", boundary)
    result = getattr(discovery, decision)(review.row.id, "editor", "Reviewed feed evidence")
    args = boundary.call_args.args
    assert args[:2] == (review.session, review.source.id)
    assert args[2].model_dump() == {
        "decision": expected,
        "actor": "editor",
        "note": "Reviewed feed evidence",
    }
    assert result["status"] == expected


@pytest.mark.parametrize("changed", [False, True])
def test_handoff_updates_pending_source_and_resets_transport_history_only_for_changed_feed(
    review, monkeypatch, changed
):
    review.row.status, review.row.selected_feed_id = "ready", review.feed.id
    review.source.feed_url = (
        "https://publisher.example/previous-feed" if changed else review.feed.url
    )
    review.source.polling_state = {"consecutive_unchanged": 4}
    review.source.last_success_at = NOW - timedelta(days=1)
    review.source.scheduled_polling_mode = "adaptive"
    review.source.scheduled_interval_seconds = 3600
    review.source.etag, review.source.last_modified = "saved-etag", "saved-date"
    review.session.scalar.return_value = None
    request = Mock()
    monkeypatch.setattr(source_enrichment, "request_enrichment", request)
    discovery.handoff(review.session, review.row)
    assert review.source.feed_url == review.feed.url
    assert review.source.approval_status == "pending" and not review.source.enabled
    request.assert_called_once_with(review.session, review.source.id, supersede=changed)
    assert review.source.polling_state == ({} if changed else {"consecutive_unchanged": 4})
    assert review.source.etag == (None if changed else "saved-etag")
    assert review.source.last_success_at == (None if changed else NOW - timedelta(days=1))
    assert review.source.scheduled_interval_seconds == (None if changed else 3600)


@pytest.mark.parametrize("unavailable", ["source", "feed", "selection", "approved"])
def test_handoff_requires_pending_source_and_selected_existing_feed(
    review, monkeypatch, unavailable
):
    review.row.selected_feed_id = review.feed.id
    if unavailable == "source":
        review.session.get.side_effect = lambda model, key: None if model is Source else review.feed
    elif unavailable == "feed":
        review.session.get.side_effect = lambda model, key: (
            review.source if model is Source else None
        )
    elif unavailable == "selection":
        review.row.selected_feed_id = None
    else:
        review.source.approval_status, review.source.enabled = "approved", True
        review.source.feed_url = review.feed.url
    request = Mock()
    monkeypatch.setattr(source_enrichment, "request_enrichment", request)
    discovery.handoff(review.session, review.row)
    request.assert_not_called()
    review.session.delete.assert_not_called()


def test_duplicate_feed_links_existing_source_without_overwriting_editorial_settings(
    review, monkeypatch
):
    review.row.status, review.row.selected_feed_id = "ready", review.feed.id
    canonical = pending_source(
        review.row,
        id=uuid.uuid4(),
        approval_status="approved",
        enabled=True,
        feed_url=review.feed.url,
        publication_policy="manual",
        reviewed_by="canonical editor",
        reviewed_at=NOW - timedelta(days=1),
    )
    review.session.scalar.return_value = canonical
    request = Mock()
    monkeypatch.setattr(source_enrichment, "request_enrichment", request)
    discovery.handoff(review.session, review.row)
    assert review.row.status == "linked" and review.row.source_id == canonical.id
    assert canonical.approval_status == "approved" and canonical.enabled
    assert canonical.publication_policy == "manual" and canonical.reviewed_by == "canonical editor"
    review.session.delete.assert_called_once_with(review.source)
    request.assert_not_called()
