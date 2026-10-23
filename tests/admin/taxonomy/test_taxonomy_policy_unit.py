"""Taxonomy HTTP commands retain reviewed identities, hashes and authenticated attribution."""

import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_admin_api import (
    topic_proposals as routes,
)
from devfeed_admin_api import (
    topic_relationships as relationships,
)
from devfeed_admin_api import (
    topics,
)
from devfeed_admin_api.auth import actor
from devfeed_core import topic_analysis, topic_relationships
from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import Topic, TopicAnalysisJob, TopicRelation
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topic_analysis import TopicAnalysisBatchOut
from devfeed_core.topics import RelationWrite

from tests.analysis.test_research_input_guards_unit import clock as clock
from tests.analysis.test_research_input_guards_unit import completed as completed
from tests.support.admin_editorial import operator_api
from tests.support.source_readiness import NOW, configuration
from tests.taxonomy.test_relationship_policy_unit import researched as researched


def queued_metadata(proposal, attribution):
    return TopicAnalysisJob(
        id=uuid.uuid4(),
        proposal_id=proposal.id,
        topic_id=None,
        status="queued",
        attempts=0,
        input_snapshot={
            "topic": copy.deepcopy(proposal.proposed),
            "evidence": copy.deepcopy(proposal.evidence),
        },
        input_hash=snapshot_hash(proposal.proposed),
        prompt_version=topic_analysis.PROMPT_VERSION,
        requested_by=attribution,
        result={},
        usage={},
        duration_ms=0,
        created_at=NOW,
        available_at=NOW,
        dispatched_at=None,
        finished_at=None,
        lease_token=None,
        lease_until=None,
        error=None,
        outcome=None,
    )


@pytest.fixture
def api(completed, monkeypatch):
    job, proposal = completed
    monkeypatch.setattr(routes, "lock_topics", Mock())
    monkeypatch.setattr(routes, "record", lambda *args, **kw: proposal)
    with operator_api(routes, relationships, topics) as value:
        value.proposal, value.job = proposal, job
        value.session.scalar.return_value = None
        yield value


@pytest.mark.parametrize(
    "failure",
    [
        ValueError("Invalid topic import"),
        OperationConflict("Preview changed"),
        RecordNotFound("Source topic unavailable"),
    ],
)
def test_topic_import_submit_preserves_domain_conflicts_and_validation_without_committing(
    api, monkeypatch, failure
):
    command = Mock(side_effect=failure)
    monkeypatch.setattr(routes.proposals, "submit_import", command)
    response = api.client.post(
        "/v1/admin/topic-imports",
        json={
            "format": "json",
            "content": "[]",
            "source_name": "Reviewed list",
            "preview_token": "a" * 64,
        },
    )
    assert response.status_code == (
        409
        if isinstance(failure, OperationConflict)
        else 404
        if isinstance(failure, RecordNotFound)
        else 422
    )
    assert response.json()["detail"] == str(failure)
    assert command.call_args.args[2] == actor(api.admin)
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("valid", [False, True])
def test_topic_import_preview_is_read_only_and_returns_its_review_token(api, monkeypatch, valid):
    result = routes.proposals.TopicImportPreview(rows=[], preview_token="a" * 64, can_submit=False)
    command = Mock(
        return_value=result,
        side_effect=None if valid else ValueError("Import must contain topic objects"),
    )
    monkeypatch.setattr(routes.proposals, "preview_import", command)
    response = api.client.post(
        "/v1/admin/topic-imports/preview",
        json={"format": "json", "content": "[]", "source_name": "Reviewed list"},
    )
    assert response.status_code == (200 if valid else 422)
    assert response.json() == (
        result.model_dump() if valid else {"detail": "Import must contain topic objects"}
    )
    api.session.commit.assert_not_called()


def test_topic_import_submit_serializes_pending_proposals_without_activating_them(api, monkeypatch):
    command = Mock(return_value=[api.proposal])
    monkeypatch.setattr(routes.proposals, "submit_import", command)
    response = api.client.post(
        "/v1/admin/topic-imports",
        json={
            "format": "json",
            "content": "[]",
            "source_name": "Reviewed list",
            "preview_token": "a" * 64,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()[0]["status"] == "pending" and response.json()[0]["applied"] is None
    assert response.json()[0]["content_hash"] == snapshot_hash(api.proposal.proposed)
    assert command.call_args.args[2] == actor(api.admin)
    api.session.commit.assert_called_once_with()


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_topic_review_forwards_full_reviewed_draft_or_rejection_with_current_hash(
    api, monkeypatch, decision
):
    proposal = api.proposal
    digest = snapshot_hash(proposal.proposed)
    body = {
        "decision": decision,
        "expected_input_hash": digest,
        "note": "Reviewed official documentation",
    }
    if decision == "approved":
        body["topic"] = copy.deepcopy(proposal.proposed)
        applied = Topic(id=uuid.uuid4(), status="active", **proposal.proposed)
        proposal.topic_id, proposal.applied = applied.id, routes.proposals.snapshot(applied)
    proposal.status, proposal.reviewed_at, proposal.reviewed_by = decision, NOW, actor(api.admin)
    proposal.review_note = body["note"]
    command = Mock(return_value=proposal)
    monkeypatch.setattr(routes.proposals, "review_proposal", command)
    response = api.client.post(f"/v1/admin/topic-proposals/{proposal.id}/review", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == decision
    session, identifier, review, attribution = command.call_args.args
    assert session is api.session and identifier == proposal.id and attribution == actor(api.admin)
    assert review.expected_input_hash == digest and review.decision == decision
    assert (review.topic is not None) is (decision == "approved")
    api.session.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "body",
    [{"decision": "approved"}, {"decision": "rejected", "topic": {"name": "Unexpected draft"}}],
)
def test_incomplete_or_contradictory_topic_reviews_never_reach_the_command(api, monkeypatch, body):
    command = Mock()
    monkeypatch.setattr(routes.proposals, "review_proposal", command)
    response = api.client.post(f"/v1/admin/topic-proposals/{api.proposal.id}/review", json=body)
    assert response.status_code == 422
    command.assert_not_called()


@pytest.mark.parametrize("barrier", ["hash", "status", "active", None])
def test_proposal_delete_requires_current_hash_status_and_idle_research(api, barrier):
    proposal = api.proposal
    active = queued_metadata(proposal, actor(api.admin))
    active.status, active.attempts = "running", 1
    active.lease_token, active.lease_until = uuid.uuid4(), NOW + timedelta(minutes=5)
    api.session.scalar.return_value = active.id if barrier == "active" else None
    response = api.client.delete(
        f"/v1/admin/topic-proposals/{proposal.id}",
        params={
            "expected_input_hash": "0" * 64
            if barrier == "hash"
            else snapshot_hash(proposal.proposed),
            "expected_status": "rejected" if barrier == "status" else "pending",
        },
    )
    assert response.status_code == (204 if barrier is None else 409)
    assert proposal.status == "pending"  # Removing review history does not approve a topic.
    if barrier is None:
        api.session.commit.assert_called_once_with()
    else:
        api.session.execute.assert_not_called()
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("operation", ["one", "all", "relationships"])
@pytest.mark.parametrize("enabled", [False, True])
def test_research_requests_require_enabled_ai_and_forward_authenticated_identity(
    api, researched, monkeypatch, operation, enabled
):
    monkeypatch.setattr(routes, "get_settings", lambda: configuration(ai_enabled=enabled))
    monkeypatch.setattr(relationships, "get_settings", lambda: configuration(ai_enabled=enabled))
    proposal, endpoints = researched
    job = queued_metadata(api.proposal, actor(api.admin))
    if operation == "relationships":
        snapshots = {
            str(identifier): topic_relationships.topic_snapshot(topic)
            for identifier, topic in endpoints.items()
        }
        snapshot = {
            "topic": snapshots[str(proposal.topic_id)],
            "related_topic_id": str(proposal.related_topic_id),
            "catalog": [
                [str(topic.id), topic.name, topic.slug, topic.kind] for topic in endpoints.values()
            ],
            "snapshots": snapshots,
            "excluded_edges": [],
        }
        job = TopicAnalysisJob(
            id=uuid.uuid4(),
            proposal_id=None,
            topic_id=proposal.topic_id,
            status="queued",
            attempts=0,
            input_snapshot=snapshot,
            input_hash=snapshot_hash(snapshot),
            prompt_version=topic_relationships.PROMPT_VERSION,
            requested_by=actor(api.admin),
            result={},
            usage={},
            duration_ms=0,
            created_at=NOW,
            available_at=NOW,
            dispatched_at=None,
            finished_at=None,
            lease_token=None,
            lease_until=None,
            error=None,
            outcome=None,
        )
    result = (
        TopicAnalysisBatchOut(queued=2, already_active=1, complete=3, pending=6)
        if operation == "all"
        else job
    )
    owner, name = (
        (relationships, "request_relationship_analysis")
        if operation == "relationships"
        else (
            routes,
            "request_all_topic_analysis" if operation == "all" else "request_topic_analysis",
        )
    )
    command = Mock(return_value=result)
    monkeypatch.setattr(owner, name, command)
    path = (
        "/v1/admin/topic-proposals/analysis"
        if operation == "all"
        else f"/v1/admin/topics/{proposal.topic_id}/relationships/analysis"
        if operation == "relationships"
        else f"/v1/admin/topic-proposals/{api.proposal.id}/analysis"
    )
    response = api.client.post(
        path,
        json={"related_topic_id": str(proposal.related_topic_id)}
        if operation == "relationships"
        else None,
    )
    assert response.status_code == (202 if enabled else 503), response.text
    if not enabled:
        command.assert_not_called()
        api.session.commit.assert_not_called()
        return
    assert command.call_args.args[0] is api.session and command.call_args.args[-1] == actor(
        api.admin
    )
    if operation == "all":
        assert response.json() == result.model_dump()
    else:
        assert response.json()["status"] == "queued" and response.json()["id"] == str(job.id)
    api.session.commit.assert_called_once_with()


def test_github_outage_remains_a_retryable_import_error_without_commit(api, monkeypatch):
    command = Mock(
        side_effect=routes.github_topics.GitHubUnavailable("GitHub public API limit reached")
    )
    monkeypatch.setattr(routes.github_topics, "pull_topics", command)
    response = api.client.post(
        "/v1/admin/topic-discovery/github", json={"revision": "a" * 40, "offset": 20}
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "github_unavailable"
    assert command.call_args.args[1].offset == 20 and command.call_args.args[2] == actor(api.admin)
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("changed", ["topic_id", "related_topic_id", "relation", None])
def test_relationship_edit_preserves_composite_identity_and_allows_evidence_only(
    changed, monkeypatch
):
    source_id, target_id = uuid.uuid4(), uuid.uuid4()
    current = TopicRelation(
        topic_id=source_id,
        related_topic_id=target_id,
        relation="uses_language",
        evidence_url="https://docs.example/old",
    )
    with operator_api(topics) as api:
        api.session.get.return_value = current
        command = Mock(return_value=current)
        monkeypatch.setattr(topics, "relate_topics", command)
        body = RelationWrite(
            topic_id=source_id,
            related_topic_id=target_id,
            relation="uses_language",
            evidence_url="https://docs.example/current",
        ).model_dump(mode="json")
        if changed:
            body[changed] = "depends_on" if changed == "relation" else str(uuid.uuid4())
        response = api.client.put(
            f"/v1/admin/topic-relations/{source_id}/{target_id}/uses_language", json=body
        )
        assert response.status_code == (200 if changed is None else 409)
        if changed:
            command.assert_not_called()
            api.session.commit.assert_not_called()
        else:
            assert command.call_args.args[1].evidence_url == "https://docs.example/current"
            api.session.commit.assert_called_once_with()


@pytest.mark.parametrize("barrier", ["hash", "status", None])
def test_relationship_proposal_deletion_preserves_current_review_identity(
    researched, monkeypatch, barrier
):
    proposal, _ = researched
    monkeypatch.setattr(relationships, "lock_topics", Mock())
    monkeypatch.setattr(relationships, "record", lambda *args, **kw: proposal)
    with operator_api(relationships) as api:
        response = api.client.delete(
            f"/v1/admin/topic-relationship-proposals/{proposal.id}",
            params={
                "expected_input_hash": "0" * 64
                if barrier == "hash"
                else relationships.proposal_hash(proposal),
                "expected_status": "approved" if barrier == "status" else "pending",
            },
        )
        assert response.status_code == (204 if barrier is None else 409)
        if barrier:
            api.session.delete.assert_not_called()
            api.session.commit.assert_not_called()
        else:
            api.session.delete.assert_called_once_with(proposal)
            api.session.commit.assert_called_once_with()
