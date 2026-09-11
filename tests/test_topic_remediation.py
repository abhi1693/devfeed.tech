"""Full automation reaches a reviewed terminal state without trusting corrections."""

import copy
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator import topic_analysis_tasks
from devfeed_core.analysis import snapshot_hash
from devfeed_core.automation_scheduler import schedule_automation
from devfeed_core.config import Settings, get_settings
from devfeed_core.models import (
    ResearchVerificationJob,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    TopicRelationProposal,
    utcnow,
)
from devfeed_core.topic_remediation import (
    ACTOR,
    RETRY_DELAY,
    finalize_relationship_reviews,
    schedule_topic_corrections,
)
from sqlalchemy import func, select
from test_topic_analysis import pending as pending
from test_topic_verification import verify_metadata


def enable(monkeypatch, *, attempts=3):
    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
    monkeypatch.setenv("DEVFEED_TOPIC_CORRECTION_MAX_ATTEMPTS", str(attempts))
    get_settings.cache_clear()


def result(item):
    return {
        "proposal_id": item["proposal_id"],
        "input_hash": item["input_hash"],
        "outcome": "ready",
        "kind": "technology",
        "description": "A software development tool.",
        "aliases": [],
        "keywords": ["example-specific"],
        "website_url": "https://example.com/",
        "logo_url": None,
        "facts": [],
        "sources": [
            {
                "url": "https://example.com/",
                "title": "Official documentation",
                "quote": "The project uses MIT licensing.",
                "fields": ["kind", "description", "keywords", "website_url"],
            }
        ],
        "reasons": ["Removed unsupported aliases and logo; retained the same entity."],
    }


def make_blocked(database, identifier, *, attempt=0, verdict=None, outcome="enriched"):
    with database.begin() as session:
        proposal = session.get(TopicProposal, uuid.UUID(identifier))
        snapshot = copy.deepcopy(proposal.proposed)
        old = utcnow() - RETRY_DELAY - timedelta(minutes=1)
        job = TopicAnalysisJob(
            proposal_id=proposal.id,
            input_hash=snapshot_hash(snapshot),
            input_snapshot={"topic": snapshot, "evidence": [], "correction": {"attempt": attempt}},
            requested_by={"subject": "test"},
            prompt_version="test",
            status="succeeded",
            outcome=outcome,
            attempts=1,
            created_at=old,
            finished_at=old,
            result={
                "applied_input_hash": snapshot_hash(snapshot),
                "topic_verification": {"check": verdict or {}},
                "reasons": [],
            },
        )
        session.add(job)
        session.flush()
        if outcome == "enriched":
            session.add(
                ResearchVerificationJob(
                    id=job.id,
                    relationships=False,
                    status="succeeded",
                    outcome="review_required",
                    finished_at=old,
                )
            )
        return job.id


def run_correction(monkeypatch, identifier, *, change=None, during=None):
    def complete(prompt, schema, **kwargs):
        assert schema["title"] == "TopicCorrectionResult"
        assert kwargs == {"allow_web_search": True}
        item = json.loads(prompt[prompt.index('{"proposal_id"') :])
        output = result(item)
        if change:
            change(output)
        if during:
            during()
        return output

    monkeypatch.setattr(
        topic_analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete)
    )
    topic_analysis_tasks._analyze(identifier)


def test_full_mode_enables_required_policies_and_validates_ai_configuration():
    values = {
        "_env_file": None,
        "database_url": "postgresql://unit/test",
        "redis_url": "redis://unit/15",
    }
    with pytest.raises(ValueError, match="AI requires"):
        Settings(**values, full_automation=True)
    settings = Settings(
        **values,
        full_automation=True,
        codex_app_server_url="unix:///run/codex.sock",
        codex_model="test",
    )
    assert all(
        getattr(settings, field)
        for field in (
            "ai_enabled",
            "auto_research_imports",
            "auto_approve_topics",
            "auto_research_relationships",
            "auto_approve_topic_relationships",
            "auto_reanalyze_topics",
        )
    )
    assert not Settings(**values).full_automation
    with pytest.raises(ValueError):
        Settings(**values, topic_correction_max_attempts=6)


@pytest.mark.integration
def test_full_mode_corrects_and_independently_approves_without_manual_actions(
    database, pending, monkeypatch
):
    enable(monkeypatch)
    with database.begin() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        proposal.proposed = {
            **proposal.proposed,
            "aliases": ["Wrong sibling"],
            "logo_url": "https://example.com/unverified.png",
        }
        original = copy.deepcopy(proposal.proposed)
        session.add(Topic(name="Peer", slug="peer", kind="technology", status="active"))
    previous = make_blocked(database, pending)
    assert schedule_automation(database)["topic_corrections_scheduled"] == 1
    assert schedule_automation(database)["topic_corrections_scheduled"] == 0
    with database() as session:
        identifier = uuid.UUID(session.get(TopicAnalysisJob, previous).result["correction_job_id"])
    run_correction(monkeypatch, identifier)
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        assert proposal.status == "pending"  # The researcher's correction is not approval.
        assert proposal.proposed["aliases"] == [] and proposal.proposed["logo_url"] is None
        assert (
            proposal.proposed["name"] == original["name"]
            and proposal.proposed["slug"] == original["slug"]
        )
        assert proposal.evidence[-1]["before"] == original
        assert proposal.evidence[-1]["after"] == proposal.proposed
        assert proposal.evidence[-1]["previous_analysis_id"] == str(previous)
    verify_metadata(monkeypatch, identifier)
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        assert proposal.status == "approved" and proposal.reviewed_by
        assert session.get(Topic, proposal.topic_id).status == "active"
    schedule_automation(database)
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(TopicAnalysisJob)
                .where(TopicAnalysisJob.topic_id.is_not(None))
            )
            >= 1
        )


@pytest.mark.integration
@pytest.mark.parametrize("case", ["exhausted", "out_of_scope", "identity"])
def test_full_mode_rejects_terminal_drafts_without_operator_action(
    database, pending, monkeypatch, case
):
    enable(monkeypatch, attempts=2)
    verdict = (
        {"relevance": {"verdict": "out_of_scope"}}
        if case == "out_of_scope"
        else {"fields": [{"field": "name", "supported": False}]}
        if case == "identity"
        else {}
    )
    previous = make_blocked(
        database, pending, attempt=2 if case == "exhausted" else 0, verdict=verdict
    )
    assert schedule_topic_corrections(database) == 0
    assert schedule_topic_corrections(database) == 0
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        assert (
            proposal.status == "rejected" and proposal.reviewed_by == ACTOR and proposal.review_note
        )
        assert session.get(TopicAnalysisJob, previous).result["correction_status"] == "rejected"
        assert session.scalar(select(func.count()).select_from(Topic)) == 0


@pytest.mark.integration
def test_corrections_are_bounded_even_when_no_optional_metadata_can_be_found(
    database, pending, monkeypatch
):
    enable(monkeypatch, attempts=2)
    previous = make_blocked(database, pending, outcome="insufficient_evidence")
    for attempt in (1, 2):
        assert schedule_topic_corrections(database) == 1
        with database() as session:
            identifier = uuid.UUID(
                session.get(TopicAnalysisJob, previous).result["correction_job_id"]
            )
            assert (
                session.get(TopicAnalysisJob, identifier).input_snapshot["correction"]["attempt"]
                == attempt
            )
        run_correction(
            monkeypatch,
            identifier,
            change=lambda output: output.update(outcome="insufficient_evidence"),
        )
        assert schedule_topic_corrections(database) == 0  # Respect delayed retry.
        with database.begin() as session:
            session.get(TopicAnalysisJob, identifier).finished_at = (
                utcnow() - RETRY_DELAY - timedelta(seconds=1)
            )
        previous = identifier
    assert schedule_topic_corrections(database) == 0
    with database() as session:
        assert session.get(TopicProposal, uuid.UUID(pending)).status == "rejected"
        assert session.scalar(select(func.count()).select_from(TopicAnalysisJob)) == 3


@pytest.mark.integration
def test_complete_and_older_unrequested_imports_enter_full_automation(
    database, pending, monkeypatch
):
    enable(monkeypatch)
    with database.begin() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        proposal.research_requested = False
        proposal.proposed = {
            **proposal.proposed,
            "aliases": ["Example"],
            "keywords": ["example"],
            "description": "Software",
            "website_url": "https://example.com/",
            "logo_url": "https://example.com/logo.png",
            "facts": [
                {
                    "name": "License",
                    "value": "MIT",
                    "source_url": "https://example.com/",
                    "retrieved_at": utcnow().isoformat(),
                }
            ],
        }
    assert schedule_topic_corrections(database) == 1
    with database() as session:
        job = session.scalar(select(TopicAnalysisJob))
        assert job.input_snapshot["correction"]["attempt"] == 0
        assert job.requested_by == ACTOR


@pytest.mark.integration
def test_mode_off_preserves_manual_review_and_pauses_queued_correction(
    database, pending, monkeypatch
):
    previous = make_blocked(database, pending, attempt=3)
    assert schedule_topic_corrections(database) == 0
    with database() as session:
        assert session.get(TopicProposal, uuid.UUID(pending)).status == "pending"
    enable(monkeypatch, attempts=5)
    assert schedule_topic_corrections(database) == 1
    with database() as session:
        identifier = uuid.UUID(session.get(TopicAnalysisJob, previous).result["correction_job_id"])
    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "false")
    get_settings.cache_clear()
    # Keep AI enabled independently, as in assisted/manual operation.
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(
        topic_analysis_tasks, "CodexClient", lambda _: pytest.fail("Paused correction invoked AI")
    )
    topic_analysis_tasks._analyze(identifier)
    with database() as session:
        job = session.get(TopicAnalysisJob, identifier)
        assert job.status == "queued" and job.attempts == 0
        assert job.available_at > utcnow()


@pytest.mark.integration
def test_changed_draft_gets_fresh_research_instead_of_rejection_from_an_old_verdict(
    database, pending, monkeypatch
):
    enable(monkeypatch)
    previous = make_blocked(database, pending, attempt=3)
    with database.begin() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        proposal.proposed = {**proposal.proposed, "description": "New input"}
    assert schedule_topic_corrections(database) == 1
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        assert proposal.status == "pending"
        identifier = uuid.UUID(session.get(TopicAnalysisJob, previous).result["correction_job_id"])
        assert session.get(TopicAnalysisJob, identifier).input_hash == snapshot_hash(
            proposal.proposed
        )
        assert (
            session.get(TopicAnalysisJob, identifier).input_snapshot["correction"]["attempt"] == 0
        )


@pytest.mark.integration
@pytest.mark.parametrize("change", ["edit", "reject", "lease"])
def test_late_correction_cannot_overwrite_newer_state(database, pending, monkeypatch, change):
    enable(monkeypatch)
    previous = make_blocked(database, pending)
    schedule_topic_corrections(database)
    with database() as session:
        identifier = uuid.UUID(session.get(TopicAnalysisJob, previous).result["correction_job_id"])

    def during():
        with database.begin() as session:
            proposal = session.get(TopicProposal, uuid.UUID(pending))
            if change == "edit":
                proposal.proposed = {**proposal.proposed, "description": "Human changed draft"}
            elif change == "reject":
                proposal.status, proposal.reviewed_by, proposal.reviewed_at = (
                    "rejected",
                    {"subject": "operator"},
                    utcnow(),
                )
            else:
                session.get(TopicAnalysisJob, identifier).lease_token = uuid.uuid4()

    run_correction(monkeypatch, identifier, during=during)
    with database() as session:
        job = session.get(TopicAnalysisJob, identifier)
        assert job.status == "running" if change == "lease" else job.outcome == "superseded"
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        assert not any(e.get("provider") == "ai_topic_correction" for e in proposal.evidence)


@pytest.mark.integration
@pytest.mark.parametrize("verification_status", ["queued", "running", "failed", "succeeded"])
def test_relationships_wait_for_verification_then_reject_in_full_mode(
    database, pending, monkeypatch, verification_status
):
    with database.begin() as session:
        first = Topic(name="First", slug="first", kind="technology", status="active")
        second = Topic(name="Second", slug="second", kind="technology", status="active")
        session.add_all([first, second])
        session.flush()
        job = TopicAnalysisJob(
            topic_id=first.id,
            input_hash="a" * 64,
            input_snapshot={},
            requested_by={},
            prompt_version="test",
            status="succeeded",
            outcome="enriched",
        )
        session.add(job)
        session.flush()
        relation = TopicRelationProposal(
            job_id=job.id,
            topic_id=first.id,
            related_topic_id=second.id,
            relation="related_to",
            evidence_url="https://example.com/",
            evidence_quote="A quoted relationship",
            explanation="Test",
            evidence_title="Official source",
            created_by={"subject": "test"},
            topic_snapshot={},
            related_topic_snapshot={},
        )
        session.add(relation)
        session.add(
            ResearchVerificationJob(
                id=job.id,
                relationships=True,
                status=verification_status,
                outcome="review_required" if verification_status == "succeeded" else None,
                finished_at=utcnow() if verification_status in {"failed", "succeeded"} else None,
            )
        )
        session.flush()
        identifier = relation.id
    assert finalize_relationship_reviews(database) == 0
    enable(monkeypatch)
    assert finalize_relationship_reviews(database) == int(
        verification_status in {"failed", "succeeded"}
    )
    with database() as session:
        assert session.get(TopicRelationProposal, identifier).status == (
            "rejected" if verification_status in {"failed", "succeeded"} else "pending"
        )
    assert finalize_relationship_reviews(database) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    "defect", ["uncited", "wrong_hash", "wrong_id", "long_quote", "unclassified", "uncovered"]
)
def test_correction_cannot_save_unbound_or_unevidenced_metadata(
    database, pending, monkeypatch, defect
):
    enable(monkeypatch)
    previous = make_blocked(database, pending)
    schedule_topic_corrections(database)
    with database() as session:
        identifier = uuid.UUID(session.get(TopicAnalysisJob, previous).result["correction_job_id"])
        original = copy.deepcopy(session.get(TopicProposal, uuid.UUID(pending)).proposed)

    def change(output):
        if defect == "uncited":
            output["sources"] = []
        elif defect == "wrong_hash":
            output["input_hash"] = "0" * 64
        elif defect == "wrong_id":
            output["proposal_id"] = str(uuid.uuid4())
        elif defect == "long_quote":
            output["sources"][0]["quote"] = "word " * 26
        elif defect == "unclassified":
            output["kind"] = "unclassified"
        else:
            output["sources"][0]["fields"] = ["kind"]

    run_correction(monkeypatch, identifier, change=change)
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        job = session.get(TopicAnalysisJob, identifier)
        assert job.status == "queued" and job.error == "invalid_analysis_result"
        assert proposal.status == "pending" and proposal.proposed == original
        assert session.scalar(select(func.count()).select_from(Topic)) == 0


@pytest.mark.integration
def test_scheduler_caps_outstanding_corrections_and_is_idempotent(database, pending, monkeypatch):
    from devfeed_core.topic_remediation import MAX_PENDING

    enable(monkeypatch)
    with database.begin() as session:
        template = session.get(TopicProposal, uuid.UUID(pending))
        for i in range(MAX_PENDING + 1):
            session.add(
                TopicProposal(
                    batch_id=template.batch_id,
                    slug=f"additional-{i}",
                    action="create",
                    origin="import",
                    source_name="Test",
                    proposed={
                        **template.proposed,
                        "name": f"Additional {i}",
                        "slug": f"additional-{i}",
                    },
                    created_by={},
                )
            )
    assert schedule_topic_corrections(database) == MAX_PENDING
    assert schedule_topic_corrections(database) == 0
    with database() as session:
        assert session.scalar(select(func.count()).select_from(TopicAnalysisJob)) == MAX_PENDING
