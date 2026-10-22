"""Branding admission and explicit corrections retain separate editorial authority."""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import topic_branding as branding
from devfeed_core import topic_corrections as corrections
from devfeed_core.analysis import snapshot_hash
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topic_auto_approval import ACTOR as AUTOMATIC
from devfeed_core.topic_proposals import snapshot, topic_draft
from devfeed_core.topics import TopicWrite
from taxonomy_command_helpers import ACTOR, NOW, proposal, topic
from test_source_readiness_unit import configuration


@pytest.fixture
def correction(monkeypatch):
    current = topic(
        kind="library", description="A JavaScript UI library.", updated_at=NOW - timedelta(hours=1)
    )
    previous = proposal(
        current,
        status="approved",
        reviewed_at=NOW - timedelta(hours=1),
        reviewed_by=AUTOMATIC,
        applied=snapshot(current),
    )
    v = SimpleNamespace(current=current, previous=previous, session=Mock(), added=[])
    v.session.scalar.side_effect = [current, previous, None]
    v.session.add.side_effect = v.added.append
    v.session.flush.side_effect = lambda: setattr(v.added[-1], "id", uuid.uuid4())
    v.review = Mock(return_value={"id": "approved-correction"})
    monkeypatch.setattr(corrections, "review_proposal", v.review)
    monkeypatch.setattr(corrections, "lock_topics", Mock())
    v.body = TopicWrite.model_validate(
        {
            **topic_draft(current).model_dump(),
            "description": "A library for web and native user interfaces.",
        }
    )
    return v


def correct(v, **changes):
    options = {
        "expected_hash": snapshot_hash(snapshot(v.current)),
        "body": v.body,
        "note": "Reviewed the official project identity and corrected its scope.",
        "actor": ACTOR,
    }
    options.update(changes)
    return corrections.correct_automatic_topic(v.session, v.current.id, **options)


@pytest.mark.parametrize("note", [" ", "x" * 1001])
def test_explicit_correction_requires_a_bounded_operator_reason(correction, note):
    with pytest.raises(ValueError, match="bounded correction reason"):
        correct(correction, note=note)
    assert correction.session.mock_calls == []


@pytest.mark.parametrize(
    "guard",
    [
        "missing",
        "stale",
        "no-approval",
        "human-approval",
        "changed-after-approval",
        "name",
        "slug",
        "pending-review",
    ],
)
def test_correction_requires_the_same_unchanged_automatically_approved_identity(correction, guard):
    v = correction
    if guard == "missing":
        v.session.scalar.side_effect = [None]
    elif guard == "stale":
        old_hash = snapshot_hash(snapshot(v.current))
        v.current.description = "A newer operator edit."
    elif guard == "no-approval":
        v.session.scalar.side_effect = [v.current, None]
    elif guard == "human-approval":
        v.previous.reviewed_by = ACTOR
    elif guard == "changed-after-approval":
        v.current.description = "A newer operator edit."
    elif guard in {"name", "slug"}:
        values = v.body.model_dump()
        values[guard] = "Another entity" if guard == "name" else "another-entity"
        v.body = TopicWrite.model_validate(values)
    else:
        pending = proposal(v.current)
        v.session.scalar.side_effect = [v.current, v.previous, pending.id]
    with pytest.raises(RecordNotFound if guard == "missing" else OperationConflict):
        correct(v, **({"expected_hash": old_hash} if guard == "stale" else {}))
    assert v.added == []
    v.review.assert_not_called()


def test_explicit_correction_adds_attributed_review_without_rewriting_automatic_approval(
    correction,
):
    v = correction
    before = snapshot(v.current)
    original = copy.deepcopy(v.previous.applied)
    reviewed = (v.previous.reviewed_by, v.previous.reviewed_at, v.previous.status)
    assert correct(v) == {"id": "approved-correction"}
    [created] = v.added
    assert created.id is not None and created.topic_id == v.current.id
    assert created.baseline == before and created.proposed == v.body.model_dump(mode="json")
    assert created.research_requested is False and created.created_by == ACTOR
    assert created.origin == "import" and created.action == "update"
    assert created.evidence[0]["previous_proposal_id"] == str(v.previous.id)
    assert created.evidence[0]["provider"] == "authorized_identity_correction"
    args = v.review.call_args.args
    assert args[:2] == (v.session, created.id) and args[-1] == ACTOR
    assert args[2].decision == "approved"
    assert args[2].expected_input_hash == snapshot_hash(created.proposed)
    assert args[2].topic.model_dump(mode="json") == created.proposed
    assert v.previous.applied == original
    assert (v.previous.reviewed_by, v.previous.reviewed_at, v.previous.status) == reviewed


@pytest.mark.parametrize("disabled", ["ai", "branding"])
def test_branding_scheduler_respects_independent_feature_admission(monkeypatch, disabled):
    settings = configuration(
        ai_enabled=disabled != "ai", auto_research_topic_branding=disabled != "branding"
    )
    monkeypatch.setattr(branding, "get_settings", lambda: settings)
    factory = Mock()
    assert branding.schedule_topic_branding(factory) == 0
    factory.begin.assert_not_called()


@pytest.mark.parametrize("state", ["lock-denied", "full", "one-slot", "two-slots", "batch-cap"])
def test_branding_builds_bounded_update_proposals_and_never_approves_the_topic(monkeypatch, state):
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    busy = 2 if state == "full" else 1 if state == "one-slot" else 0
    settings = configuration(
        ai_enabled=True,
        auto_research_topic_branding=True,
        automation_batch_size=1 if state == "batch-cap" else 5,
    )
    monkeypatch.setattr(branding, "get_settings", lambda: settings)
    monkeypatch.setattr(branding, "lock_topics", Mock())
    session.scalar.side_effect = [state != "lock-denied", busy]
    count = 0 if state in {"lock-denied", "full"} else 2 if state == "two-slots" else 1
    rows = [topic(name, kind="library") for name in ("React", "Angular")][:count]
    session.scalars.return_value.all.return_value = rows
    original = [snapshot(row) for row in rows]
    assert branding.schedule_topic_branding(factory) == count
    added = [call.args[0] for call in session.add.call_args_list]
    assert len(added) == count * 2
    for index, current in enumerate(rows):
        proposed, job = added[index * 2 : index * 2 + 2]
        assert proposed.topic_id == current.id and proposed.baseline == original[index]
        assert proposed.source_name == branding.VERSION and proposed.action == "update"
        assert proposed.origin == "ai_analysis" and proposed.created_by == branding.ACTOR
        assert proposed.proposed == topic_draft(current).model_dump(mode="json")
        assert job.proposal_id == proposed.id and job.topic_id is None
        assert job.input_hash == snapshot_hash(proposed.proposed)
        assert job.input_snapshot == {"topic": proposed.proposed, "evidence": [], "branding": True}
        assert job.requested_by == branding.ACTOR and job.prompt_version == branding.VERSION
        assert snapshot(current) == original[index]
    if count == 0:
        session.add.assert_not_called()
