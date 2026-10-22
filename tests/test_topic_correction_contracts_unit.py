"""Research corrections need current identity and evidence; they never approve drafts."""

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import TopicAnalysisJob, TopicProposal, utcnow
from devfeed_core.topic_remediation import (
    ACTOR,
    PROMPT_VERSION,
    TopicCorrectionResult,
    _enqueue,
    apply_topic_correction,
    correction_prompt,
)
from devfeed_core.topics import TopicWrite

URL = "https://deltadb.example/docs"


def correction():
    draft = TopicWrite(
        name="DeltaDB",
        slug="deltadb",
        kind="technology",
        description="A developer database with documented query APIs.",
        aliases=["Unverified old name"],
        keywords=["deltadb"],
        website_url="https://deltadb.example/",
    ).model_dump(mode="json")
    proposal = TopicProposal(
        id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        slug="deltadb",
        action="create",
        status="pending",
        origin="import",
        proposed=draft,
        evidence=[{"kind": "import", "name": "operator draft"}],
        research_requested=True,
    )
    previous = uuid.uuid4()
    job = TopicAnalysisJob(
        id=uuid.uuid4(),
        proposal_id=proposal.id,
        input_hash=snapshot_hash(draft),
        input_snapshot={
            "topic": copy.deepcopy(draft),
            "correction": {"previous_job_id": str(previous), "attempt": 1},
        },
        result={"existing_receipt": "preserve"},
        status="running",
        attempts=1,
        lease_token=uuid.uuid4(),
        lease_until=utcnow() + timedelta(minutes=3),
        available_at=utcnow() - timedelta(seconds=1),
        created_at=utcnow(),
        requested_by=ACTOR,
        prompt_version=PROMPT_VERSION,
    )
    result = TopicCorrectionResult(
        proposal_id=proposal.id,
        input_hash=job.input_hash,
        outcome="ready",
        kind="technology",
        description="A documented developer database with query APIs.",
        aliases=[],
        keywords=["deltadb"],
        website_url="https://deltadb.example/",
        logo_url=None,
        facts=[],
        sources=[
            {
                "url": URL,
                "title": "Official documentation",
                "quote": "DeltaDB provides query APIs for application developers.",
                "fields": ["kind", "description", "keywords", "website_url"],
            }
        ],
        reasons=["Removed the unsupported alias while retaining the same database identity."],
    )
    return proposal, job, result


def test_sourced_correction_retains_identity_and_full_audit_without_approving():
    proposal, job, result = correction()
    before = copy.deepcopy(proposal.proposed)
    evidence = copy.deepcopy(proposal.evidence)

    assert apply_topic_correction(proposal, job, result) == "enriched"

    assert proposal.status == "pending" and proposal.reviewed_by is None
    assert proposal.proposed["name"] == before["name"]
    assert proposal.proposed["slug"] == before["slug"]
    assert proposal.proposed["aliases"] == []
    assert proposal.proposed["description"] == result.description
    assert proposal.evidence[:-1] == evidence
    audit = proposal.evidence[-1]
    assert audit["before"] == before and audit["after"] == proposal.proposed
    assert set(audit["fields"]) == {"aliases", "description"}
    assert audit["analysis_id"] == str(job.id)
    assert audit["previous_analysis_id"] == job.input_snapshot["correction"]["previous_job_id"]
    assert audit["attempt"] == 1 and audit["actor"] == ACTOR
    assert audit["sources"] == [source.model_dump(mode="json") for source in result.sources]
    assert job.result == {
        "existing_receipt": "preserve",
        "applied_input_hash": snapshot_hash(proposal.proposed),
    }


@pytest.mark.parametrize("change", ["already-reviewed", "draft-changed"])
def test_stale_correction_leaves_draft_and_audit_untouched(change):
    proposal, job, result = correction()
    if change == "already-reviewed":
        proposal.status = "rejected"
        proposal.reviewed_by = {"subject": "editor"}
        proposal.reviewed_at = utcnow()
        proposal.review_note = "Insufficient relevance evidence"
    else:
        proposal.proposed = {**proposal.proposed, "description": "New operator evidence."}
    before = copy.deepcopy(proposal.proposed)
    evidence = copy.deepcopy(proposal.evidence)
    assert apply_topic_correction(proposal, job, result) == "superseded"
    assert proposal.proposed == before and proposal.evidence == evidence
    assert job.result == {"existing_receipt": "preserve"}


@pytest.mark.parametrize("mismatch", ["proposal", "input"])
def test_result_cannot_be_applied_to_another_proposal_or_input(mismatch):
    proposal, job, result = correction()
    if mismatch == "proposal":
        result.proposal_id = uuid.uuid4()
    else:
        result.input_hash = "f" * 64
    before = copy.deepcopy(proposal.proposed)
    with pytest.raises(ValueError, match="exact pending proposal"):
        apply_topic_correction(proposal, job, result)
    assert proposal.proposed == before and len(proposal.evidence) == 1


def test_insufficient_evidence_preserves_the_pending_draft_and_existing_receipts():
    proposal, job, result = correction()
    result.outcome = "insufficient_evidence"
    result.sources = []
    before = copy.deepcopy(proposal.proposed)
    assert apply_topic_correction(proposal, job, result) == "insufficient_evidence"
    assert proposal.proposed == before and proposal.status == "pending"
    assert job.result == {"existing_receipt": "preserve"}


@pytest.mark.parametrize("missing", ["sources", "kind"])
def test_ready_correction_requires_evidenced_kind_and_sources(missing):
    proposal, job, result = correction()
    setattr(result, missing, [] if missing == "sources" else "unclassified")
    with pytest.raises(ValueError, match="evidenced identity and kind"):
        apply_topic_correction(proposal, job, result)
    assert proposal.status == "pending" and len(proposal.evidence) == 1


def test_correction_rejects_oversized_quotes_without_partial_application():
    proposal, job, result = correction()
    result.sources[0].quote = " ".join(["evidence"] * 26)
    before = copy.deepcopy(proposal.proposed)
    with pytest.raises(ValueError, match="at most 25 words"):
        apply_topic_correction(proposal, job, result)
    assert proposal.proposed == before


@pytest.mark.parametrize("field", ["description", "aliases", "keywords", "website_url", "logo_url"])
def test_retained_nonempty_metadata_requires_field_specific_support(field):
    proposal, job, result = correction()
    if field == "aliases":
        result.aliases = ["Other name"]
    elif field == "logo_url":
        result.logo_url = "https://deltadb.example/logo.png"
    result.sources[0].fields = [name for name in result.sources[0].fields if name != field]
    before = copy.deepcopy(proposal.proposed)
    with pytest.raises(ValueError, match="Every retained correction field needs a source"):
        apply_topic_correction(proposal, job, result)
    assert proposal.proposed == before and len(proposal.evidence) == 1


def test_corrected_fact_cannot_cite_a_url_outside_the_research_sources():
    proposal, job, result = correction()
    from devfeed_core.topic_analysis import ResearchFact

    result.facts = [ResearchFact(name="License", value="MIT", source_url="https://other.example/")]
    result.sources[0].fields.append("facts")
    with pytest.raises(ValueError, match="Each corrected fact must cite a research source"):
        apply_topic_correction(proposal, job, result)
    assert len(proposal.evidence) == 1


def test_retained_facts_keep_retrieval_receipts_and_new_facts_receive_their_own():
    proposal, job, result = correction()
    retained = {
        "name": "License",
        "value": "MIT",
        "source_url": URL,
        "retrieved_at": datetime(2025, 1, 1, tzinfo=UTC).isoformat(),
    }
    proposal.proposed = TopicWrite.model_validate(
        {**proposal.proposed, "facts": [retained]}
    ).model_dump(mode="json")
    job.input_hash = snapshot_hash(proposal.proposed)
    job.input_snapshot["topic"] = copy.deepcopy(proposal.proposed)
    result.input_hash = job.input_hash
    from devfeed_core.topic_analysis import ResearchFact

    result.facts = [
        ResearchFact(name="License", value="MIT", source_url=URL),
        ResearchFact(name="Interface", value="SQL", source_url=URL),
    ]
    result.sources[0].fields.append("facts")
    result.sources[
        0
    ].quote = "DeltaDB is an MIT-licensed database with SQL query APIs for application developers."

    assert apply_topic_correction(proposal, job, result) == "enriched"
    persisted = proposal.proposed["facts"][0]
    assert {key: persisted[key] for key in ("name", "value", "source_url")} == {
        key: retained[key] for key in ("name", "value", "source_url")
    }
    assert datetime.fromisoformat(persisted["retrieved_at"]) == datetime.fromisoformat(
        retained["retrieved_at"]
    )
    assert proposal.proposed["facts"][1]["name"] == "Interface"
    assert datetime.fromisoformat(proposal.proposed["facts"][1]["retrieved_at"]) > datetime(
        2025, 1, 1, tzinfo=UTC
    )


@pytest.mark.parametrize("reset", [False, True])
def test_queued_correction_preserves_only_relevant_failed_evidence_and_deep_copies_input(reset):
    proposal, previous, _ = correction()
    previous.input_snapshot["correction"]["attempt"] = 2
    previous.status, previous.outcome = "succeeded", "review_required"
    previous.finished_at = utcnow()
    previous.lease_token = previous.lease_until = None
    previous.result = {
        "reasons": ["Website could not be verified"],
        "topic_verification": {"supported": False},
        "evidence_verification": {
            "checks": {
                "valid": {"status": "verified", "url": URL},
                "invalid": {"status": "unavailable", "url": "https://missing.example/"},
            }
        },
    }
    added = []

    def flush():
        added[0].id = uuid.uuid4()

    session = SimpleNamespace(add=added.append, flush=flush)
    queued = _enqueue(session, proposal, previous, reset=reset)

    assert added == [queued] and not proposal.research_requested
    assert queued.input_hash == snapshot_hash(proposal.proposed)
    assert queued.requested_by == ACTOR and queued.prompt_version == PROMPT_VERSION
    intent = queued.input_snapshot["correction"]
    assert intent["attempt"] == (0 if reset else 3)
    assert intent["previous_job_id"] == str(previous.id)
    assert intent["feedback"]["failed_citations"] == (
        [] if reset else [{"status": "unavailable", "url": "https://missing.example/"}]
    )
    assert intent["feedback"]["research_reasons"] == (
        [] if reset else ["Website could not be verified"]
    )
    assert previous.result["correction_status"] == "queued"
    assert previous.result["correction_job_id"] == str(queued.id)
    proposal.proposed["aliases"].append("Later edit")
    assert "Later edit" not in queued.input_snapshot["topic"]["aliases"]


def test_first_correction_has_no_previous_job_or_failed_citation_feedback():
    proposal, _, _ = correction()
    added = []
    queued = _enqueue(SimpleNamespace(add=added.append, flush=lambda: None), proposal)
    assert queued.input_snapshot["correction"]["previous_job_id"] is None
    assert queued.input_snapshot["correction"]["attempt"] == 0
    assert queued.input_snapshot["correction"]["feedback"] == {
        "research_reasons": [],
        "topic_verification": {},
        "failed_citations": [],
    }


def test_correction_prompt_preserves_exact_identity_and_current_feedback_as_data():
    proposal, job, _ = correction()
    snapshot = {
        "topic": proposal.proposed,
        "correction": {"proposal_id": str(proposal.id), "feedback": {"reason": "check evidence"}},
    }
    prompt = correction_prompt(snapshot)
    payload = json.loads(prompt[prompt.index('{"proposal_id"') :])
    assert payload == {
        "proposal_id": str(proposal.id),
        "input_hash": job.input_hash,
        "topic": proposal.proposed,
        "feedback": {"reason": "check evidence"},
    }
