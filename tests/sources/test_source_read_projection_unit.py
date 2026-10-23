"""Assembly of preselected source/job read models; SQL latest-row selection is not simulated."""

import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import source_enrichment, source_logos
from devfeed_core import source_lifecycle as lifecycle
from devfeed_core.models import ArticleImageJob, IngestionJob, SourceCandidate, SourceDiscoveryJob

from tests.support.source_readiness import NOW, assessment, configuration, job, profile, source


def candidate(current=None, **changes):
    return SourceCandidate(
        **{
            "id": uuid.uuid4(),
            "source_id": current.id if current else None,
            "identity_url": current.website_url if current else "https://other-publisher.example/",
            "name": current.name if current else "Other publisher",
            "status": "linked" if current else "pending",
            "approval_status": "pending",
            "created_at": NOW - timedelta(days=1),
            "updated_at": NOW - timedelta(hours=1),
            **changes,
        }
    )


def test_source_read_model_assembles_each_preselected_job_and_separates_unlinked_candidates():
    current = source(
        poll_interval_seconds=1800,
        polling_mode="fixed",
        scheduled_polling_mode="fixed",
        consecutive_failures=0,
    )
    linked, other = candidate(current), candidate()
    discovery = job(
        SourceDiscoveryJob, candidate_id=other.id, stage="discover", result={"background": True}
    )
    completed = profile(current)
    completed.created_at = NOW - timedelta(hours=1)
    completed.finished_at = NOW - timedelta(minutes=50)
    reviewed = assessment(current, completed)
    reviewed.created_at = NOW - timedelta(minutes=40)
    reviewed.available_at = NOW - timedelta(minutes=39)
    reviewed.finished_at = NOW - timedelta(minutes=30)
    # An ingestion row belongs to an independently approved source.
    approved = source(
        approval="approved",
        poll_interval_seconds=1800,
        polling_mode="fixed",
        scheduled_polling_mode="fixed",
        consecutive_failures=0,
    )
    ingestion = job(IngestionJob, source_id=approved.id)
    session = Mock()
    session.scalars.side_effect = [[linked], [discovery], [completed], [reviewed], [], [ingestion]]
    result = lifecycle.source_readiness_batch(
        session, [current, approved], [other], settings=configuration(ai_enabled=True), now=NOW
    )
    facts = result[f"source:{current.id}"]
    assert facts.source is current and facts.candidate is linked
    assert facts.jobs["profile"] is completed and facts.jobs["assessment"] is reviewed
    assert facts.readiness.can_approve and not facts.readiness.can_ingest
    assert len(facts.snapshot) == 64
    assert result[f"source:{approved.id}"].jobs["ingestion"] is ingestion
    unlinked = result[f"candidate:{other.id}"]
    assert unlinked.source is None and unlinked.candidate is other
    assert unlinked.jobs["discovery"] is discovery and unlinked.readiness.stage == "discovery"
    assert unlinked.readiness.state == "waiting"
    session.add.assert_not_called()
    session.execute.assert_not_called()


def test_empty_readiness_batch_does_not_query_for_any_job_subjects():
    session = Mock()
    assert lifecycle.source_readiness_batch(session, [], settings=configuration(), now=NOW) == {}
    session.scalars.assert_not_called()


@pytest.mark.parametrize(
    "restriction", ["grandfathered", "profile-running", "branding-failed", "assessment-required"]
)
def test_activation_checks_only_required_processing_and_preserves_approved_polling(
    monkeypatch, restriction
):
    current = source(approval="approved", enabled=False)
    completed = profile(current)
    tasks = {"profile": completed}
    settings = configuration(
        ai_enabled=True, image_storage_enabled=True, image_public_url="https://images.example"
    )
    if restriction != "grandfathered":
        current.processing_request = {
            "kind": "upstream_retry",
            "id": str(uuid.uuid4()),
            "target": "profile",
            "requested_at": NOW.isoformat(),
            "required": [
                "profile",
                "branding",
                *(["assessment"] if restriction == "assessment-required" else []),
            ],
        }
    if restriction == "profile-running":
        tasks["profile"] = profile(current, status="running")
    elif restriction == "branding-failed":
        current.logo_url = "https://publisher.example/logo.png"
        tasks["branding"] = job(
            ArticleImageJob,
            source_id=current.id,
            operation="source-logo-refresh",
            image_url=current.logo_url,
            storage={},
            storage_version="v1",
            status="failed",
        )
    facts = lifecycle.SourceLifecycleFacts(
        key=f"source:{current.id}", source=current, candidates=[], jobs=tasks
    )
    facts.readiness = lifecycle.derive_source_readiness(
        current, jobs=tasks, settings=settings, now=NOW
    )
    monkeypatch.setattr(lifecycle, "source_readiness", lambda *_: facts)
    blockers = lifecycle.source_activation_blockers(Mock(), current)
    if restriction == "grandfathered":
        assert blockers == []
    elif restriction == "profile-running":
        assert "profile_running" in blockers
    elif restriction == "branding-failed":
        assert blockers == ["branding_failed"]
    else:
        assert blockers == ["assessment_missing"]
    assert not current.enabled and current.approval_status == "approved"


@pytest.mark.parametrize("action", ["assessment", "branding"])
def test_paused_required_assessment_or_missing_branding_is_queued_without_granting_review_authority(
    monkeypatch, action
):
    current = source(approval="approved" if action == "assessment" else "pending", enabled=False)
    completed = profile(current)
    settings = configuration(
        ai_enabled=True,
        image_storage_enabled=True,
        image_public_url="https://images.example",
        full_automation=False,
    )
    if action == "assessment":
        current.processing_request = {
            "kind": "upstream_retry",
            "id": str(uuid.uuid4()),
            "target": "assessment",
            "requested_at": NOW.isoformat(),
            "required": ["profile", "branding", "assessment"],
        }
    else:
        current.logo_url = "https://publisher.example/logo.png"
    facts = lifecycle.SourceLifecycleFacts(
        key=f"source:{current.id}", source=current, candidates=[], jobs={"profile": completed}
    )
    facts.readiness = lifecycle.derive_source_readiness(
        current, jobs=facts.jobs, settings=settings, now=NOW
    )
    assert facts.readiness.next_action == action
    monkeypatch.setattr(lifecycle, "source_readiness", lambda *_: facts)
    monkeypatch.setattr(lifecycle, "get_settings", lambda: settings)
    relevance, logo = Mock(), Mock()
    monkeypatch.setattr(source_enrichment, "request_relevance", relevance)
    monkeypatch.setattr(source_logos, "request_source_logo", logo)
    session = Mock()
    assert lifecycle.reconcile_source_processing(session, current) is facts
    if action == "assessment":
        relevance.assert_called_once_with(session, current.id, profile=completed, automatic=False)
        logo.assert_not_called()
    else:
        logo.assert_called_once_with(session, current.id, automatic=True)
        relevance.assert_not_called()
    assert not current.enabled
    session.flush.assert_called_once()
