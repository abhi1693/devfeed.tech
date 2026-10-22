"""Research requests retain manual decisions and chain only approved current identities."""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import article_topic_policy
from devfeed_core import topic_analysis as research
from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import Topic, TopicAnalysisJob
from devfeed_core.services import OperationConflict, RecordNotFound
from taxonomy_command_helpers import ACTOR, NOW, metadata_job, proposal, relationship_job, topic
from test_source_readiness_unit import configuration


@pytest.fixture
def runtime(monkeypatch):
    value = SimpleNamespace(
        session=Mock(),
        proposal=proposal(),
        settings=configuration(ai_enabled=True, auto_research_relationships=False),
    )
    monkeypatch.setattr(research, "get_settings", lambda: value.settings)
    monkeypatch.setattr(article_topic_policy, "get_settings", lambda: value.settings)
    value.lock = Mock()
    monkeypatch.setattr(research, "lock_topics", value.lock)
    return value


@pytest.mark.parametrize("guard", ["missing", "reviewed", "article-paused", "complete"])
def test_metadata_admission_requires_an_allowed_pending_draft_with_missing_fields(runtime, guard):
    if guard == "reviewed":
        runtime.proposal.status, runtime.proposal.reviewed_at, runtime.proposal.reviewed_by = (
            "rejected",
            NOW,
            ACTOR,
        )
    elif guard == "article-paused":
        runtime.proposal.origin = "article_enrichment"
        runtime.settings = configuration(article_topic_proposals_enabled=False)
    elif guard == "complete":
        runtime.proposal.proposed.update(
            description="A UI component library.",
            aliases=["ReactJS"],
            keywords=["react-components"],
            website_url="https://react.dev/",
            logo_url="https://react.dev/logo.png",
            facts=[
                {
                    "name": "Documentation",
                    "value": "Official developer reference",
                    "source_url": "https://react.dev/",
                    "retrieved_at": NOW.isoformat(),
                }
            ],
        )
    runtime.session.scalar.side_effect = [None if guard == "missing" else runtime.proposal, None]
    with pytest.raises(RecordNotFound if guard == "missing" else OperationConflict):
        research.request_topic_analysis(runtime.session, runtime.proposal.id, ACTOR)
    runtime.session.add.assert_not_called()


@pytest.mark.parametrize("active", [False, True])
def test_metadata_request_reuses_live_work_or_freezes_a_new_draft_snapshot(runtime, active):
    job = TopicAnalysisJob(
        id=uuid.uuid4(),
        proposal_id=runtime.proposal.id,
        status="running",
        attempts=1,
        created_at=NOW - timedelta(minutes=2),
        available_at=NOW - timedelta(minutes=1),
        lease_token=uuid.uuid4(),
        lease_until=NOW + timedelta(minutes=5),
        input_snapshot={"topic": copy.deepcopy(runtime.proposal.proposed), "evidence": []},
        input_hash=snapshot_hash(runtime.proposal.proposed),
        requested_by=ACTOR,
        prompt_version=research.PROMPT_VERSION,
    )
    runtime.session.scalar.side_effect = [runtime.proposal, job if active else None]
    before = copy.deepcopy(runtime.proposal.proposed)
    output = research.request_topic_analysis(runtime.session, runtime.proposal.id, ACTOR)
    if active:
        assert output is job and output.lease_token == job.lease_token
        runtime.session.add.assert_not_called()
    else:
        assert output.proposal_id == runtime.proposal.id and output.requested_by == ACTOR
        assert output.input_hash == snapshot_hash(before)
        assert output.input_snapshot == {"topic": before, "evidence": []}
        assert output.prompt_version == research.PROMPT_VERSION
    assert runtime.proposal.status == "pending" and runtime.proposal.proposed == before


def test_batch_admission_reports_active_complete_and_missing_drafts_separately(runtime):
    missing, active, complete = proposal(), proposal(), proposal()
    for row, name in zip((missing, active, complete), ("React", "Angular", "Vue"), strict=True):
        row.slug = name.lower()
        row.proposed.update(name=name, slug=row.slug)
    complete.proposed.update(
        description="Developer UI library.",
        aliases=["VueJS"],
        keywords=["vue-components"],
        website_url="https://vuejs.org/",
        logo_url="https://vuejs.org/logo.svg",
        facts=[
            {
                "name": "Docs",
                "value": "Official reference",
                "source_url": "https://vuejs.org/",
                "retrieved_at": NOW.isoformat(),
            }
        ],
    )
    rows = sorted([missing, active, complete], key=lambda row: row.id)
    runtime.session.scalars.side_effect = [SimpleNamespace(all=lambda: rows), [active.id]]
    result = research.request_all_topic_analysis(runtime.session, ACTOR)
    assert result.model_dump() == {"queued": 1, "already_active": 1, "complete": 1, "pending": 3}
    queued = runtime.session.add.call_args.args[0]
    assert queued.proposal_id == missing.id and queued.input_hash == snapshot_hash(missing.proposed)
    assert all(row.status == "pending" for row in rows)


@pytest.fixture
def approved(runtime):
    current = topic(kind="library")
    runtime.proposal = proposal(current)
    runtime.analysis = metadata_job(runtime.proposal)
    current.description = runtime.proposal.proposed["description"]
    runtime.proposal.status, runtime.proposal.reviewed_at, runtime.proposal.reviewed_by = (
        "approved",
        NOW,
        ACTOR,
    )
    runtime.proposal.applied = {
        "id": str(current.id),
        "status": "active",
        "draft": runtime.proposal.proposed,
    }
    runtime.topic = current
    runtime.peer = topic("JavaScript", kind="language")
    runtime.followup = relationship_job(current, runtime.peer, status="queued")
    runtime.session.get.side_effect = lambda model, identifier: (
        current if model is Topic and identifier == current.id else None
    )
    return runtime


@pytest.mark.parametrize(
    "guard",
    [
        "ai-off",
        "not-approved",
        "coverage-scheduler",
        "missing-topic",
        "inactive-topic",
        "no-research",
    ],
)
def test_chaining_requires_ai_approved_active_identity_and_successful_metadata(
    approved, monkeypatch, guard
):
    if guard == "ai-off":
        approved.settings = configuration(ai_enabled=False, full_automation=False)
    elif guard == "not-approved":
        approved.proposal.topic_id = None
        approved.proposal.status, approved.proposal.applied = "pending", None
        approved.proposal.reviewed_at = approved.proposal.reviewed_by = None
    elif guard == "coverage-scheduler":
        approved.settings = configuration(ai_enabled=True, auto_research_relationships=True)
    elif guard == "missing-topic":
        approved.session.get.side_effect = None
        approved.session.get.return_value = None
    elif guard == "inactive-topic":
        approved.topic.status = "rejected"
    else:
        approved.session.scalar.return_value = None
    request = Mock()
    monkeypatch.setattr(research, "request_relationship_analysis", request)
    assert (
        research.queue_relationships_after_enrichment(
            approved.session,
            approved.proposal,
            approved.analysis if guard != "no-research" else None,
        )
        is None
    )
    request.assert_not_called()


@pytest.mark.parametrize("mode", ["new", "active", "linked"])
def test_chaining_links_one_current_run_without_resetting_completed_or_live_work(
    approved, monkeypatch, mode
):
    if mode == "linked":
        approved.analysis.result["relationship_analysis_id"] = str(approved.followup.id)
        approved.session.get.side_effect = lambda model, identifier: (
            approved.topic
            if model is Topic
            else approved.followup
            if identifier == approved.followup.id
            else None
        )
    approved.session.scalar.return_value = approved.followup if mode == "active" else None
    request = Mock(return_value=approved.followup)
    monkeypatch.setattr(research, "request_relationship_analysis", request)
    result = research.queue_relationships_after_enrichment(
        approved.session, approved.proposal, approved.analysis
    )
    assert result is approved.followup
    assert approved.analysis.result["relationship_analysis_id"] == str(result.id)
    if mode == "new":
        assert request.call_args.args[1] == approved.topic.id
        assert request.call_args.args[3] == approved.analysis.requested_by
    else:
        request.assert_not_called()
    assert approved.followup.status == "queued" and approved.proposal.status == "approved"


def test_blocked_followup_retains_metadata_and_actionable_reason_then_clears_reason_on_recovery(
    approved, monkeypatch
):
    approved.session.scalar.return_value = None
    request = Mock(side_effect=OperationConflict("Choose a specific related topic"))
    monkeypatch.setattr(research, "request_relationship_analysis", request)
    before = copy.deepcopy(approved.proposal.proposed)
    assert (
        research.queue_relationships_after_enrichment(
            approved.session, approved.proposal, approved.analysis
        )
        is None
    )
    assert (
        approved.analysis.result["relationship_analysis_error"] == "Choose a specific related topic"
    )
    assert approved.proposal.proposed == before and approved.proposal.status == "approved"
    request.side_effect, request.return_value = None, approved.followup
    assert (
        research.queue_relationships_after_enrichment(
            approved.session, approved.proposal, approved.analysis
        )
        is approved.followup
    )
    assert "relationship_analysis_error" not in approved.analysis.result


def test_a_superseded_relationship_run_resumes_only_its_approved_metadata_parents(
    approved, monkeypatch
):
    obsolete = relationship_job(approved.topic, approved.peer, outcome="superseded")
    old_identity = {
        **obsolete.input_snapshot["topic"],
        "description": "Earlier project documentation.",
    }
    obsolete.input_snapshot["topic"] = old_identity
    obsolete.input_snapshot["snapshots"][str(approved.topic.id)] = old_identity
    obsolete.input_hash = snapshot_hash(obsolete.input_snapshot)
    approved.analysis.result["relationship_analysis_id"] = str(obsolete.id)
    approved.session.scalar.return_value = True  # Already-scoped existence/read results.
    approved.session.scalars.return_value.all.return_value = [approved.analysis]
    approved.session.get.side_effect = None
    approved.session.get.return_value = approved.proposal
    chain = Mock()
    monkeypatch.setattr(research, "queue_relationships_after_enrichment", chain)
    research.resume_relationships_after_superseded(approved.session, obsolete)
    chain.assert_called_once_with(approved.session, approved.proposal, approved.analysis)
    approved.lock.assert_called_once_with(approved.session)


def test_unlinked_superseded_research_does_not_start_unrelated_followups(runtime, monkeypatch):
    runtime.session.scalar.return_value = False
    chain = Mock()
    monkeypatch.setattr(research, "queue_relationships_after_enrichment", chain)
    research.resume_relationships_after_superseded(runtime.session, Mock(id=uuid.uuid4()))
    chain.assert_not_called()
    runtime.lock.assert_not_called()
