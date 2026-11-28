"""The source board reads durable facts and delegates only explicit guarded actions."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from devfeed_core.models import (
    ArticleImageJob,
    IngestionJob,
    Source,
    SourceCandidate,
    SourceDiscoveryJob,
    SourceEnrichmentJob,
    SourceReview,
    utcnow,
)
from devfeed_core.source_enrichment import source_identity
from devfeed_core.source_relevance import VERSION
from sqlalchemy import func, select

pytestmark = pytest.mark.integration
PATH = "/v1/admin/source-pipeline"


def seed_source(session, name="Publisher", *, age=0, approval="pending", enabled=False, feed=True):
    timestamp = utcnow() - timedelta(days=age)
    source = Source(
        name=name,
        feed_url=f"https://{uuid.uuid4().hex}.example/feed" if feed else None,
        website_url=f"https://{uuid.uuid4().hex}.example/",
        source_type="publisher",
        approval_status=approval,
        enabled=enabled,
        created_at=timestamp,
        updated_at=timestamp,
        last_success_at=timestamp if approval == "approved" else None,
    )
    session.add(source)
    session.flush()
    return source


def candidate_for(session, source=None, *, name="Candidate", age=0):
    timestamp = utcnow() - timedelta(days=age)
    candidate = SourceCandidate(
        name=name,
        identity_url=f"https://{uuid.uuid4().hex}.example/",
        source_id=source.id if source else None,
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(candidate)
    session.flush()
    return candidate


def detail_path(identifier, kind="source"):
    return f"{PATH}/items/{kind}/{identifier}"


def counts(session):
    return {
        model.__tablename__: session.scalar(select(func.count()).select_from(model))
        for model in (
            Source,
            SourceCandidate,
            SourceDiscoveryJob,
            SourceEnrichmentJob,
            IngestionJob,
            SourceReview,
            ArticleImageJob,
        )
    }


def test_rejected_source_offers_explicit_reopen_without_creating_work(admin_client, database):
    with database.begin() as session:
        identifier = seed_source(session, approval="rejected").id
    path = detail_path(identifier)
    response = admin_client.get(path)
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["stage"] == "rejected" and item["can_advance"]
    assert item["next_action"] == "reopen" and item["advance_label"] == "Reopen for review"
    assert not item["can_approve"] and not item["can_ingest"]
    assert item["retry_targets"] == []
    assert (
        admin_client.post(
            path + "/retry", json={"expected_snapshot": item["snapshot"], "target": "profile"}
        ).status_code
        == 409
    )
    with database() as session:
        assert session.get(Source, identifier).approval_status == "rejected"
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 0


def test_reopen_is_snapshot_guarded_and_preserves_manual_review_authority(
    admin_client, database, monkeypatch
):
    from devfeed_aggregator import source_tasks
    from devfeed_core.config import get_settings

    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "full_automation", True)
    original_time = utcnow() - timedelta(days=30)
    with database.begin() as session:
        source = seed_source(session, approval="rejected")
        source.reviewed_by, source.review_note, source.reviewed_at = (
            "original-reviewer",
            "Original rejection",
            original_time,
        )
        session.add(
            SourceReview(
                source_id=source.id,
                decision="rejected",
                actor="original-reviewer",
                note="Original rejection",
                created_at=original_time,
            )
        )
        identifier = source.id
        before = counts(session)
    path = detail_path(identifier)
    snapshot = admin_client.get(path).json()["item"]["snapshot"]
    with database.begin() as session:
        session.get(Source, identifier).name = "Corrected publisher name"
    stale = admin_client.post(path + "/advance", json={"expected_snapshot": snapshot})
    assert stale.status_code == 409
    with database() as session:
        assert counts(session) == before
    snapshot = admin_client.get(path).json()["item"]["snapshot"]
    payload = {"expected_snapshot": snapshot}
    reopened = admin_client.post(path + "/advance", json=payload)
    assert reopened.status_code == 200, reopened.text
    item = reopened.json()["item"]
    assert item["approval_status"] == "pending" and not item["enabled"]
    assert item["stage"] == "profile" and not item["can_approve"]
    assert admin_client.post(path + "/advance", json=payload).status_code == 409
    with database() as session:
        source = session.get(Source, identifier)
        assert source.processing_request["manual_review"] is True
        assert source.reviewed_by == "original-reviewer" and source.reviewed_at == original_time
        assert source.review_note == "Original rejection"
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 1
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 1
        assert session.scalar(select(IngestionJob.id)) is None
        profile_id = session.scalar(select(SourceEnrichmentJob.id))
    decision = {"decision": "approved", "note": "Operator reviewed the current publisher"}
    assert (
        admin_client.post(f"/v1/admin/sources/{identifier}/review", json=decision).status_code
        == 409
    )
    monkeypatch.setattr(
        source_tasks,
        "assess_source",
        lambda *a, **kw: {
            "approval_supported": True,
            "reason": "Relevant engineering feed evidence",
        },
    )
    source_tasks.enrich_source(str(profile_id))
    with database() as session:
        assessment_id = session.scalar(
            select(SourceEnrichmentJob.id).where(
                SourceEnrichmentJob.operation == "relevance", SourceEnrichmentJob.status == "queued"
            )
        )
        assert assessment_id is not None
    source_tasks.enrich_source(str(assessment_id))
    ready = admin_client.get(path).json()["item"]
    assert ready["approval_status"] == "pending" and not ready["enabled"]
    assert ready["can_approve"] and not ready["can_ingest"]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 1
        assert session.scalar(select(IngestionJob.id)) is None
    approved = admin_client.post(f"/v1/admin/sources/{identifier}/review", json=decision)
    assert approved.status_code == 200, approved.text
    with database() as session:
        assert session.get(Source, identifier).processing_request == {}
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 2
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 1


def test_reopening_rejected_source_without_feed_queues_discovery(admin_client, database):
    with database.begin() as session:
        source = seed_source(session, approval="rejected", feed=False)
        candidate = candidate_for(session, source)
        candidate.status = candidate.approval_status = "rejected"
        identifier = source.id
    path = detail_path(identifier)
    item = admin_client.get(path).json()["item"]
    response = admin_client.post(path + "/advance", json={"expected_snapshot": item["snapshot"]})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["stage"] == "discovery"
    assert response.json()["item"]["approval_status"] == "pending"
    with database() as session:
        assert session.scalar(select(func.count()).select_from(SourceDiscoveryJob)) == 1
        assert session.scalar(select(SourceEnrichmentJob.id)) is None
        assert session.scalar(select(IngestionJob.id)) is None
        candidate = session.scalar(select(SourceCandidate))
        assert candidate.status == candidate.approval_status == "pending"


def test_standalone_rejected_candidate_has_no_reopen_shortcut(admin_client, database):
    with database.begin() as session:
        candidate = candidate_for(session)
        candidate.status = candidate.approval_status = "rejected"
        identifier = candidate.id
    path = detail_path(identifier, "candidate")
    item = admin_client.get(path).json()["item"]
    assert item["stage"] == "rejected" and not item["can_advance"]
    assert not item["can_approve"] and item["next_action"] is None
    assert (
        admin_client.post(
            path + "/advance", json={"expected_snapshot": item["snapshot"]}
        ).status_code
        == 409
    )
    with database() as session:
        assert session.scalar(select(Source.id)) is None
        assert session.scalar(select(SourceDiscoveryJob.id)) is None


def test_rejected_source_with_current_prerequisites_offers_only_editorial_reapproval(
    admin_client, database
):
    with database.begin() as session:
        source = seed_source(session, approval="rejected")
        session.add(
            SourceEnrichmentJob(
                source_id=source.id,
                operation="profile",
                status="succeeded",
                result={"input": source_identity(source), "sample": []},
            )
        )
        session.add(
            SourceReview(
                source_id=source.id,
                decision="rejected",
                actor="original-reviewer",
                note="Old review",
            )
        )
        identifier = source.id
        before = counts(session)
    path = detail_path(identifier)
    response = admin_client.get(path)
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["stage"] == "rejected" and item["can_approve"]
    assert not item["can_advance"] and not item["can_ingest"]
    assert item["next_action"] is None and item["retry_targets"] == []
    with database() as session:
        assert counts(session) == before
    for suffix, payload in (
        ("/advance", {"expected_snapshot": item["snapshot"]}),
        ("/retry", {"expected_snapshot": item["snapshot"], "target": "profile"}),
    ):
        assert admin_client.post(path + suffix, json=payload).status_code == 409
    decision = admin_client.post(
        f"/v1/admin/sources/{identifier}/review",
        json={"decision": "approved", "note": "Operator rechecked the current feed"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["approval_status"] == "approved"
    with database() as session:
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 2
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 1
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 1


def test_detail_exposes_selected_evidence_and_job_status_without_lease_secrets(
    admin_client, database
):
    with database.begin() as session:
        source = seed_source(session)
        source.description = "Publisher description"
        source.relevance_assessment = {
            "version": "source-relevance-v5",
            "reason": "Grounded source evidence",
            "feed_url": source.feed_url,
            "source_type": "publisher",
        }
        candidate = candidate_for(session, source)
        session.add(
            SourceEnrichmentJob(
                source_id=source.id,
                operation="profile",
                status="failed",
                error="Feed unavailable",
                result={"input": source_identity(source)},
                lease_token=uuid.uuid4(),
            )
        )
        session.add(
            SourceReview(
                source_id=source.id, decision="rejected", actor="operator", note="Historical review"
            )
        )
        identifier, candidate_id = source.id, candidate.id
    response = admin_client.get(detail_path(identifier))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile"]["description"] == "Publisher description"
    assert body["assessment"]["reason"] == "Grounded source evidence"
    assert body["candidate_ids"] == [str(candidate_id)]
    assert body["tasks"][0]["error"] == "Feed unavailable"
    assert body["reviews"][0]["actor"] == "operator"
    assert "lease_token" not in response.text and "submitted_by" not in response.text
    linked = admin_client.get(detail_path(candidate_id, "candidate"))
    assert linked.status_code == 200
    assert linked.json()["item"]["key"] == f"source:{identifier}"
    assert linked.json()["item"]["snapshot"] == body["item"]["snapshot"]
    stale_identity = admin_client.post(
        detail_path(candidate_id, "candidate") + "/advance",
        json={"expected_snapshot": body["item"]["snapshot"]},
    )
    assert stale_identity.status_code == 409
    assert admin_client.get(detail_path(uuid.uuid4())).status_code == 404


def test_existing_job_views_include_source_operation_and_result(admin_client, database):
    with database.begin() as session:
        source = seed_source(session)
        job = SourceEnrichmentJob(
            source_id=source.id,
            operation="profile",
            status="succeeded",
            result={
                "input": source_identity(source),
                "sample": [{"title": "Engineering evidence"}],
            },
        )
        session.add(job)
        session.flush()
        job_id = str(job.id)
    detail = admin_client.get(f"/v1/admin/jobs/source-enrichment/{job_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["details"]["operation"] == "profile"
    assert detail.json()["details"]["result"]["sample"] == [{"title": "Engineering evidence"}]
    listing = admin_client.get("/v1/admin/jobs/source-enrichment").json()
    assert listing["items"][0]["details"]["operation"] == "profile"


def test_stale_advance_rejected_without_queuing_or_approving(admin_client, database):
    with database.begin() as session:
        identifier = seed_source(session).id
    snapshot = admin_client.get(detail_path(identifier)).json()["item"]["snapshot"]
    with database.begin() as session:
        session.get(Source, identifier).name = "Edited after board loaded"
        before = counts(session)
    response = admin_client.post(
        detail_path(identifier) + "/advance", json={"expected_snapshot": snapshot}
    )
    assert response.status_code == 409, response.text
    with database() as session:
        assert counts(session) == before
        assert session.get(Source, identifier).approval_status == "pending"


def test_explicit_profile_retry_is_exact_and_duplicate_request_conflicts(admin_client, database):
    with database.begin() as session:
        source = seed_source(session)
        failed = SourceEnrichmentJob(
            source_id=source.id,
            operation="profile",
            status="failed",
            error="network_error",
            result={"input": source_identity(source)},
        )
        session.add(failed)
        session.flush()
        identifier, job_id = source.id, failed.id
    path = detail_path(identifier)
    item = admin_client.get(path).json()["item"]
    assert {"stage": "profile", "job_id": str(job_id)} in item["retry_targets"]
    wrong = admin_client.post(
        path + "/retry",
        json={
            "expected_snapshot": item["snapshot"],
            "target": "profile",
            "job_id": str(uuid.uuid4()),
        },
    )
    assert wrong.status_code == 409
    payload = {"expected_snapshot": item["snapshot"], "target": "profile", "job_id": str(job_id)}
    response = admin_client.post(path + "/retry", json=payload)
    assert response.status_code == 200, response.text
    assert admin_client.post(path + "/retry", json=payload).status_code == 409
    with database() as session:
        jobs = list(
            session.scalars(
                select(SourceEnrichmentJob).where(SourceEnrichmentJob.source_id == identifier)
            )
        )
        assert len(jobs) == 2 and sum(job.status == "queued" for job in jobs) == 1
        source = session.get(Source, identifier)
        assert source.approval_status == "pending" and not source.enabled


def test_advance_is_explicit_and_does_not_approve_or_start_ingestion(admin_client, database):
    with database.begin() as session:
        identifier = seed_source(session).id
    path = detail_path(identifier)
    item = admin_client.get(path).json()["item"]
    response = admin_client.post(path + "/advance", json={"expected_snapshot": item["snapshot"]})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["key"] == f"source:{identifier}"
    with database() as session:
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 1
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 0
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 0
        source = session.get(Source, identifier)
        assert source.approval_status == "pending" and not source.enabled


def test_concurrent_exact_retry_accepts_one_snapshot_and_creates_one_outbox(admin_client, database):
    with database.begin() as session:
        source = seed_source(session)
        failed = SourceEnrichmentJob(
            source_id=source.id,
            operation="profile",
            status="failed",
            error="network_error",
            result={"input": source_identity(source)},
        )
        session.add(failed)
        session.flush()
        identifier, job_id = source.id, failed.id
    path = detail_path(identifier)
    snapshot = admin_client.get(path).json()["item"]["snapshot"]
    payload = {"expected_snapshot": snapshot, "target": "profile", "job_id": str(job_id)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: admin_client.post(path + "/retry", json=payload), range(2))
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(SourceEnrichmentJob)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(SourceEnrichmentJob)
                .where(SourceEnrichmentJob.status.in_(["queued", "running"]))
            )
            == 1
        )


def test_manual_approval_remains_available_when_only_ai_assessment_failed(
    admin_client, database, monkeypatch
):
    from devfeed_core.config import get_settings

    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "full_automation", True)
    with database.begin() as session:
        source = seed_source(session)
        profile = SourceEnrichmentJob(
            source_id=source.id,
            operation="profile",
            status="succeeded",
            result={"input": source_identity(source), "sample": []},
        )
        session.add(profile)
        session.flush()
        session.add(
            SourceEnrichmentJob(
                source_id=source.id,
                operation="relevance",
                status="failed",
                error="analysis_unavailable",
                result={
                    "input": source_identity(source),
                    "profile_job_id": str(profile.id),
                    "policy_version": VERSION,
                },
            )
        )
        identifier = source.id
    response = admin_client.get(detail_path(identifier))
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["stage"] == "assessment" and item["state"] == "blocked"
    assert item["can_approve"] and not item["can_ingest"]
    decision = admin_client.post(
        f"/v1/admin/sources/{identifier}/review",
        json={"decision": "approved", "note": "Operator verified the original feed"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["approval_status"] == "approved"


def test_active_upstream_retry_pauses_then_requires_explicit_resume(admin_client, database):
    from devfeed_aggregator.source_tasks import enrich_source

    with database.begin() as session:
        source = seed_source(session, approval="approved", enabled=True)
        identifier = source.id
    path = detail_path(identifier)
    initial = admin_client.get(path).json()["item"]
    assert initial["stage"] == "active" and initial["can_ingest"]
    response = admin_client.post(
        path + "/retry",
        json={
            "expected_snapshot": initial["snapshot"],
            "target": "profile",
            "job_id": None,
        },
    )
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["stage"] == "profile" and not item["enabled"]
    assert item["approval_status"] == "approved" and not item["can_ingest"]
    with database() as session:
        job_id = session.scalar(select(SourceEnrichmentJob.id))
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 0
    enrich_source(str(job_id))
    settled = admin_client.get(path).json()["item"]
    assert settled["stage"] == "paused" and settled["can_advance"]
    assert settled["advance_label"] == "Resume" and not settled["can_ingest"]
    resumed = admin_client.post(path + "/advance", json={"expected_snapshot": settled["snapshot"]})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["item"]["enabled"]
    assert resumed.json()["item"]["stage"] == "ingestion"
    assert not resumed.json()["item"]["can_ingest"]  # The requested fetch is already queued.
    with database() as session:
        assert session.scalar(select(func.count()).select_from(SourceReview)) == 0
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 1


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 51},
        {"terminal_since": "2020-01-01T00:00:00Z"},
        {"terminal_since": "2026-01-01T00:00:00"},
        {"source_type": "unknown"},
    ],
)
def test_board_rejects_invalid_queries(admin_client, params):
    assert admin_client.get(PATH, params=params).status_code == 422


@pytest.mark.parametrize("cursor", [-1, 2**31])
def test_column_rejects_invalid_cursor(admin_client, cursor):
    assert (
        admin_client.get(PATH + "/columns/discovery", params={"cursor": cursor}).status_code == 422
    )


def test_mutation_validation_cannot_smuggle_editorial_changes(admin_client, database):
    with database.begin() as session:
        identifier = seed_source(session).id
    path = detail_path(identifier)
    snapshot = admin_client.get(path).json()["item"]["snapshot"]
    assert (
        admin_client.post(
            path + "/advance", json={"expected_snapshot": snapshot, "enabled": True}
        ).status_code
        == 422
    )
    assert (
        admin_client.post(
            path + "/retry", json={"expected_snapshot": snapshot, "target": "active"}
        ).status_code
        == 422
    )
    assert (
        admin_client.post(path + "/advance", json={"expected_snapshot": "stale"}).status_code == 422
    )
    assert admin_client.get(PATH + "/columns/nonexistent").status_code == 422
