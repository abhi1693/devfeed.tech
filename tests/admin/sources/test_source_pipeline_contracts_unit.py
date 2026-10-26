"""Source-board presentation preserves editorial intent and guarded operator actions."""

import uuid
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest
from devfeed_admin_api import source_pipeline as pipeline
from devfeed_core.models import Source, SourceCandidate, SourceDiscoveryJob, SourceReview
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.source_lifecycle import (
    SourceLifecycleFacts,
    derive_source_readiness,
    source_snapshot_token,
)
from fastapi import HTTPException

from tests.support.source_readiness import NOW, configuration, job, profile, source


def facts(current=None, *, candidate=None, jobs=None):
    value = SourceLifecycleFacts(
        key=f"source:{current.id}" if current is not None else f"candidate:{candidate.id}",
        source=current,
        candidates=[candidate] if candidate is not None else [],
        jobs=jobs or {},
    )
    value.readiness = derive_source_readiness(
        current, candidates=value.candidates, jobs=value.jobs, settings=configuration(), now=NOW
    )
    value.snapshot = source_snapshot_token(value)
    return value


def candidate(**changes):
    return SourceCandidate(
        **{
            "id": uuid.uuid4(),
            "name": "Suggested publisher",
            "identity_url": "https://candidate.example/",
            "approval_status": "pending",
            "status": "pending",
            "created_at": NOW - timedelta(days=3),
            "updated_at": NOW - timedelta(days=2),
            **changes,
        }
    )


def query(monkeypatch, **changes):
    monkeypatch.setattr(pipeline, "utcnow", lambda: NOW)
    return pipeline.SourcePipelineQuery(
        **dict(q="", limit=2, source_type=None, needs_attention=False, **changes)
    )


@pytest.mark.parametrize(
    "enabled,stage,label", [(True, "active", None), (False, "paused", "Resume")]
)
def test_approved_source_card_preserves_polling_intent_without_revoking_review(
    enabled, stage, label
):
    current = source(approval="approved", enabled=enabled)
    value = facts(current)

    card = pipeline._card(value)

    assert (card.kind, card.id, card.source_id) == ("source", current.id, current.id)
    assert card.approval_status == "approved" and card.enabled is enabled
    assert card.stage == stage and card.advance_label == label
    assert card.can_ingest is enabled and not card.can_approve
    assert card.snapshot == value.snapshot
    assert card.feed_url == current.feed_url and card.website_url == current.website_url
    assert current.approval_status == "approved" and current.enabled is enabled


def test_candidate_failure_card_and_detail_explain_exact_retry_without_fabricating_source():
    proposed = candidate(status="unresolved")
    failed = job(SourceDiscoveryJob, candidate_id=proposed.id, stage="discover", status="failed")
    value = facts(candidate=proposed, jobs={"discovery": failed})

    detail = pipeline._detail(object(), value)

    assert detail.item.kind == "candidate" and detail.item.source_id is None
    assert detail.item.candidate_id == proposed.id and not detail.item.enabled
    assert detail.item.website_url == proposed.identity_url and detail.item.feed_url is None
    assert detail.item.stage == "discovery" and detail.item.state == "blocked"
    assert detail.item.retry_targets == [
        pipeline.SourcePipelineRetryTarget(stage="discovery", job_id=failed.id)
    ]
    assert detail.blockers == ["feed_discovery_unresolved"]
    assert [(task.stage, task.status, task.error) for task in detail.tasks] == [
        ("discovery", "failed", "Upstream unavailable")
    ]
    assert detail.profile.model_dump() == dict.fromkeys(pipeline.SourcePipelineProfile.model_fields)
    assert detail.reviews == [] and detail.assessment is None
    assert detail.candidate_ids == [proposed.id]


def test_source_detail_contains_current_evidence_review_attribution_and_last_task_activity():
    current = source(approval="approved", description="Engineering systems", language="en")
    failed = profile(current, status="failed", finished_at=NOW)
    linked = candidate(source_id=current.id, status="linked")
    value = facts(current, candidate=linked, jobs={"profile": failed, "assessment": None})
    review = SourceReview(
        source_id=current.id,
        decision="approved",
        actor="admin-reviewer",
        note="Evaluate the engineering feed",
        created_at=NOW - timedelta(days=1),
    )
    session = SimpleNamespace(scalars=lambda _: [review])

    detail = pipeline._detail(session, value)

    assert detail.item.updated_at == NOW
    assert detail.profile.description == "Engineering systems" and detail.profile.language == "en"
    assert detail.item.name == current.name and detail.item.candidate_id == linked.id
    assert [(check.key, check.status) for check in detail.checks if check.key == "profile"] == [
        ("profile", "failed")
    ]
    assert detail.reviews[0].actor == "admin-reviewer"
    assert detail.reviews[0].note == "Evaluate the engineering feed"
    assert [task.job_id for task in detail.tasks] == [failed.id]


@pytest.mark.parametrize(
    "since,message",
    [
        (NOW.replace(tzinfo=None), "include a timezone"),
        (NOW - timedelta(days=90, seconds=1), "last 90 days"),
        (NOW + timedelta(seconds=1), "last 90 days"),
    ],
)
def test_terminal_history_rejects_ambiguous_or_unbounded_ranges(monkeypatch, since, message):
    with pytest.raises(HTTPException, match=message) as error:
        query(monkeypatch, terminal_since=since)
    assert error.value.status_code == 422


def test_terminal_history_normalizes_equivalent_timezones_and_search(monkeypatch):
    request = query(
        monkeypatch,
        terminal_since=(NOW - timedelta(days=1)).astimezone(
            timezone(timedelta(hours=5, minutes=30))
        ),
    )
    assert request.terminal_since == NOW - timedelta(days=1)
    assert request.terminal_since.utcoffset() == timedelta(0)


@pytest.mark.parametrize("action", ["advance", "retry"])
def test_actions_forward_concurrency_snapshot_and_authenticated_actor(monkeypatch, action):
    current = source()
    value = facts(current)
    received, completed = [], []
    session = SimpleNamespace(
        scalars=lambda _: [],
        commit=lambda: completed.append("commit"),
        close=lambda: completed.append("close"),
    )
    identity = SimpleNamespace(subject="authenticated-admin")
    if action == "advance":
        body = pipeline.SourcePipelineAdvance(expected_snapshot=value.snapshot)
        monkeypatch.setattr(
            pipeline,
            "advance_source_processing",
            lambda *args, **kw: received.append((args, kw)) or value,
        )
    else:
        body = pipeline.SourcePipelineRetry(
            expected_snapshot=value.snapshot, target="profile", job_id=uuid.uuid4()
        )
        monkeypatch.setattr(
            pipeline,
            "retry_source_processing",
            lambda *args, **kw: received.append((args, kw)) or value,
        )

    result = getattr(pipeline, action)("source", current.id, body, session, identity)

    expected = (session, "source", current.id, value.snapshot)
    if action == "retry":
        expected += ("profile", body.job_id)
    assert received == [(expected, {"actor": "authenticated-admin"})]
    assert result.item.snapshot == value.snapshot and result.item.id == current.id
    assert completed == ["commit", "close"]


@pytest.mark.parametrize("action", ["advance", "retry"])
def test_changed_snapshot_conflict_is_preserved_without_committing(monkeypatch, action):
    def changed(*args, **kwargs):
        raise OperationConflict("Source changed; reload the board")

    monkeypatch.setattr(pipeline, f"{action}_source_processing", changed)
    body = (
        pipeline.SourcePipelineAdvance(expected_snapshot="a" * 64)
        if action == "advance"
        else pipeline.SourcePipelineRetry(expected_snapshot="a" * 64, target="profile")
    )
    with pytest.raises(OperationConflict, match="Source changed"):
        getattr(pipeline, action)(
            "source", uuid.uuid4(), body, object(), SimpleNamespace(subject="admin")
        )


@pytest.mark.parametrize("linked", [False, True])
def test_candidate_detail_follows_promotion_without_losing_the_board_item(monkeypatch, linked):
    current = source() if linked else None
    proposed = candidate(
        source_id=current.id if current is not None else None,
        status="linked" if linked else "pending",
    )
    value = facts(current, candidate=proposed)
    loaded, closed = [], []

    def get(model, identifier):
        loaded.append((model, identifier))
        return proposed if model is SourceCandidate else current

    session = SimpleNamespace(get=get, scalars=lambda _: [], close=lambda: closed.append(True))
    monkeypatch.setattr(pipeline, "source_readiness_batch", lambda *_: {value.key: value})

    result = pipeline.detail("candidate", proposed.id, session)

    assert loaded == [(SourceCandidate, proposed.id)] + ([(Source, current.id)] if linked else [])
    assert result.item.kind == ("source" if linked else "candidate")
    assert result.item.id == (current.id if linked else proposed.id)
    assert closed == [True]


@pytest.mark.parametrize("kind", ["candidate", "source"])
def test_missing_detail_has_reload_guidance(kind):
    with pytest.raises(RecordNotFound, match="reload the board"):
        pipeline.detail(kind, uuid.uuid4(), SimpleNamespace(get=lambda *_: None))
