"""Group scoped source/candidate rows without simulating repository SQL selection."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from devfeed_core import source_lifecycle
from devfeed_core.models import ArticleImageJob, IngestionJob, SourceDiscoveryJob
from test_source_lifecycle_commands_unit import candidate
from test_source_readiness_unit import NOW, assessment, configuration, job, profile, source


def test_source_cards_keep_each_sources_jobs_and_prefer_its_active_discovery(monkeypatch):
    first, second = source(), source(approval="approved")
    historical = candidate(first, created_at=NOW - timedelta(days=3))
    active = candidate(first, status="pending", created_at=NOW - timedelta(days=1))
    unresolved = candidate()
    linked_elsewhere = candidate(second)
    running = job(
        SourceDiscoveryJob, candidate_id=active.id, stage="discover", status="running", result={}
    )
    completed = job(
        SourceDiscoveryJob,
        candidate_id=historical.id,
        stage="discover",
        status="succeeded",
        result={},
    )
    queued = job(SourceDiscoveryJob, candidate_id=unresolved.id, stage="discover", result={})
    current_profile = profile(first)
    current_assessment = assessment(first, current_profile)
    branding = job(
        ArticleImageJob, source_id=first.id, operation="source-logo", status="succeeded", storage={}
    )
    ingestion = job(IngestionJob, source_id=second.id, status="succeeded")
    latest = Mock(
        side_effect=[
            {active.id: running, historical.id: completed, unresolved.id: queued},
            {first.id: current_profile},
            {first.id: current_assessment},
            {first.id: branding},
            {second.id: ingestion},
        ]
    )
    monkeypatch.setattr(source_lifecycle, "_latest", latest)
    session = Mock()
    session.scalars.return_value = [historical, active, linked_elsewhere]
    cards = source_lifecycle.source_readiness_batch(
        session, [first, second], [unresolved, active], settings=configuration(), now=NOW
    )
    assert set(cards) == {f"source:{first.id}", f"source:{second.id}", f"candidate:{unresolved.id}"}
    primary = cards[f"source:{first.id}"]
    assert [row.id for row in primary.candidates] == [active.id, historical.id]
    assert primary.jobs == {
        "discovery": running,
        "profile": current_profile,
        "assessment": current_assessment,
        "branding": branding,
        "ingestion": None,
    }
    assert cards[f"source:{second.id}"].jobs["ingestion"] is ingestion
    assert cards[f"candidate:{unresolved.id}"].jobs["discovery"] is queued
    assert cards[f"candidate:{unresolved.id}"].source is None
    assert primary.snapshot == source_lifecycle.source_snapshot_token(primary)


def test_unresolved_candidate_card_does_not_inherit_an_unrelated_sources_completed_work(
    monkeypatch,
):
    unresolved = candidate()
    latest = Mock(side_effect=[{}, {}, {}, {}, {}])
    monkeypatch.setattr(source_lifecycle, "_latest", latest)
    session = Mock()
    cards = source_lifecycle.source_readiness_batch(
        session, [], [unresolved], settings=configuration(), now=NOW
    )
    value = cards[f"candidate:{unresolved.id}"]
    assert value.readiness.next_action == "discovery"
    assert not value.readiness.can_approve and all(task is None for task in value.jobs.values())
    session.scalars.assert_not_called()


def test_empty_readiness_request_returns_no_phantom_cards(monkeypatch):
    monkeypatch.setattr(source_lifecycle, "_latest", lambda *_args, **_kwargs: {})
    assert (
        source_lifecycle.source_readiness_batch(Mock(), [], settings=configuration(), now=NOW) == {}
    )


def test_single_source_readiness_uses_the_batch_policy(monkeypatch):
    current = source()
    expected = SimpleNamespace(source=current, snapshot=uuid.uuid4().hex)
    batch = Mock(return_value={f"source:{current.id}": expected})
    monkeypatch.setattr(source_lifecycle, "source_readiness_batch", batch)
    session, settings = Mock(), configuration()
    assert (
        source_lifecycle.source_readiness(session, current, settings=settings, now=NOW) is expected
    )
    batch.assert_called_once_with(session, [current], settings=settings, now=NOW)
