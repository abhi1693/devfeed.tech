"""Imported aliases and complete drafts cannot bypass independent verification."""

import copy
import json
import uuid
from types import SimpleNamespace

import pytest
from devfeed_aggregator import research_verification_tasks as tasks
from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.models import (
    ResearchVerificationJob,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    utcnow,
)
from devfeed_core.research_evidence import VERIFICATION_VERSION, citation_key
from devfeed_core.research_verification import POLICY_VERSION, schedule_verification
from devfeed_core.topic_verification import (
    FIELDS,
    VERSION,
    checked_verdict,
    topic_verified,
    verification_input,
)
from sqlalchemy import select
from test_topic_analysis import pending as pending
from test_topic_analysis import run_metadata_research

SOURCE = {"url": "https://example.com/", "quote": "The project uses MIT licensing."}


def output(item):
    return {
        "proposal_id": item["proposal_id"],
        "input_hash": item["input_hash"],
        "verdict": "supported",
        "relevance": {
            "verdict": "in_scope",
            "sources": [0],
            "reason": "Primary documentation establishes a software development tool.",
        },
        "fields": [
            {"field": f, "supported": True, "sources": [0], "reason": "Verified field"}
            for f in FIELDS
            if item["topic"].get(f)
        ],
        "aliases": [
            {"alias": a, "same_identity": True, "sources": [0], "reason": "Same entity"}
            for a in item["topic"].get("aliases", [])
        ],
        "sources": [SOURCE],
    }


def evidence(values):
    return {
        "version": VERIFICATION_VERSION,
        "checks": {
            citation_key(url, quote): {"url": url, "quote": quote, "status": "verified"}
            for url, quote in values
        },
    }


def mock_verifier(monkeypatch, *, change=None, during=None):
    calls = []

    def complete(prompt, schema, **kwargs):
        item = json.loads(prompt[prompt.index('{"proposal_id"') :])
        result = output(item)
        if change:
            change(result)
        if during:
            during()
        calls.append(item)
        return result

    monkeypatch.setattr(tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    return calls


def verify_metadata(monkeypatch, identifier, **kwargs):
    from devfeed_core.db import session_factory

    schedule_verification(session_factory())
    calls = mock_verifier(monkeypatch, **kwargs)
    monkeypatch.setattr(tasks, "verify_citations", evidence)
    tasks.verify_research(str(identifier))
    return calls


def enable(monkeypatch):
    monkeypatch.setenv("DEVFEED_AUTO_APPROVE_TOPICS", "true")
    get_settings.cache_clear()


@pytest.mark.parametrize("defect", ["alias", "field", "source", "hash", "duplicate", "boolean"])
def test_full_draft_validation_rejects_missing_and_mismatched_evidence(defect):
    proposal = TopicProposal(
        id=uuid.uuid4(),
        proposed={"name": "RSS", "slug": "rss", "kind": "technology", "aliases": ["rfc4287"]},
    )
    item = verification_input(proposal)
    result = output(item)
    if defect == "alias":
        result["aliases"] = []
    if defect == "field":
        result["fields"] = result["fields"][:-1]
    if defect == "source":
        result["aliases"][0]["sources"] = []
    if defect == "hash":
        result["input_hash"] = "0" * 64
    if defect == "duplicate":
        result["aliases"] *= 2
    if defect == "boolean":
        result["aliases"][0]["same_identity"] = "true"
    with pytest.raises(ValueError):
        checked_verdict(result, item)
    assert not topic_verified({"version": VERSION, "check": result}, proposal, {})


@pytest.mark.parametrize("verdict", ["out_of_scope", "uncertain"])
def test_factually_correct_entertainment_cannot_pass_without_developer_relevance(verdict):
    proposal = TopicProposal(
        id=uuid.uuid4(),
        proposed={"name": "Yu-Gi-Oh!", "slug": "yugioh", "kind": "game", "aliases": []},
    )
    item = verification_input(proposal)
    result = output(item)
    # Even an inconsistent overall supported verdict cannot bypass this gate.
    result["relevance"] = {
        "verdict": verdict,
        "sources": [],
        "reason": "A trading card franchise, with no established developer purpose.",
    }
    verified = checked_verdict(result, item)
    assert not topic_verified(verified, proposal, evidence([(SOURCE["url"], SOURCE["quote"])]))


@pytest.mark.parametrize("defect", ["missing", "uncited", "invalid_index", "unverified"])
def test_relevance_needs_its_own_verified_evidence(defect):
    proposal = TopicProposal(
        id=uuid.uuid4(), proposed={"name": "Example", "slug": "example", "kind": "technology"}
    )
    item = verification_input(proposal)
    result = output(item)
    if defect == "missing":
        del result["relevance"]
    elif defect == "uncited":
        result["relevance"]["sources"] = []
    elif defect == "invalid_index":
        result["relevance"]["sources"] = [99]
    else:
        result["sources"].append(
            {"url": "https://example.com/developers", "quote": "An SDK for developers."}
        )
        result["relevance"]["sources"] = [1]
    if defect != "unverified":
        with pytest.raises(ValueError):
            checked_verdict(result, item)
    assert not topic_verified(
        {"version": VERSION, "check": result},
        proposal,
        evidence([(SOURCE["url"], SOURCE["quote"])]),
    )


def test_evidenced_developer_topic_passes_but_legacy_identity_only_verdict_does_not():
    proposal = TopicProposal(
        id=uuid.uuid4(), proposed={"name": "Example SDK", "slug": "example", "kind": "technology"}
    )
    item = verification_input(proposal)
    verified = checked_verdict(output(item), item)
    citations = evidence([(SOURCE["url"], SOURCE["quote"])])
    assert topic_verified(verified, proposal, citations)
    assert not topic_verified({**verified, "version": "topic-identity-v1"}, proposal, citations)


def test_unclassified_import_cannot_be_approved_even_if_model_passes_it():
    proposal = TopicProposal(
        id=uuid.uuid4(),
        proposed={
            "name": "Mathematics",
            "slug": "mathematics",
            "kind": "unclassified",
            "aliases": [],
        },
    )
    item = verification_input(proposal)
    verified = checked_verdict(output(item), item)
    assert not topic_verified(verified, proposal, evidence([(SOURCE["url"], SOURCE["quote"])]))


def test_research_resolves_unclassified_kind_with_evidence():
    from devfeed_core.topic_analysis import TopicResearchResult, apply_topic_research
    from test_topic_analysis import output as research_output

    proposal = TopicProposal(
        id=uuid.uuid4(),
        proposed={
            "name": "Mathematics",
            "slug": "mathematics",
            "kind": "unclassified",
            "aliases": [],
        },
        status="pending",
        evidence=[],
    )
    result = research_output(kind="discipline")
    result["sources"][0]["fields"].append("kind")
    job = TopicAnalysisJob(
        id=uuid.uuid4(), input_hash=snapshot_hash(proposal.proposed), model="fixture"
    )
    assert (
        apply_topic_research(proposal, job, TopicResearchResult.model_validate(result))
        == "enriched"
    )
    assert proposal.proposed["kind"] == "discipline"


@pytest.mark.integration
@pytest.mark.parametrize("relevance", ["out_of_scope", "uncertain"])
def test_scope_review_blocks_catalog_writes_even_with_supported_identity(
    admin_client, database, pending, monkeypatch, relevance
):
    enable(monkeypatch)
    with database.begin() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        proposal.proposed = {**proposal.proposed, "name": "Yu-Gi-Oh!", "kind": "game"}
    identifier = run_metadata_research(admin_client, pending, monkeypatch, verify=False)

    def outside_scope(result):
        result["relevance"] = {
            "verdict": relevance,
            "sources": [],
            "reason": "Entertainment franchise without an established developer purpose.",
        }

    verify_metadata(monkeypatch, identifier, change=outside_scope)
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        assert proposal.status == "pending" and proposal.topic_id is None
        assert session.scalar(select(Topic)) is None
        job = session.get(TopicAnalysisJob, identifier)
        assert job.result["topic_verification"]["check"]["relevance"]["verdict"] == relevance
        assert job.result["auto_approval"][-1]["status"] == "blocked"
        assert "relevance" in job.result["auto_approval"][-1]["reason"]
        task = session.get(ResearchVerificationJob, identifier)
        if relevance == "out_of_scope":
            assert task.outcome == "review_required"
        else:
            assert task.status == "queued" and task.error == "topic_verification_uncertain"


@pytest.mark.integration
@pytest.mark.parametrize("alias", ["rfc4287", "paper", "hdl", "ilp"])
def test_bad_imported_alias_stays_pending_despite_verified_research(
    admin_client, database, pending, monkeypatch, alias
):
    enable(monkeypatch)
    with database.begin() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        proposal.proposed = {**proposal.proposed, "aliases": [alias]}
    identifier = run_metadata_research(admin_client, pending, monkeypatch, verify=False)
    with database() as session:
        assert session.get(TopicProposal, uuid.UUID(pending)).status == "pending"
        assert session.scalar(select(Topic)) is None

    def unsupported(result):
        result["aliases"][0]["same_identity"] = False
        result["verdict"] = "unsupported"

    verify_metadata(monkeypatch, identifier, change=unsupported)
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        assert proposal.status == "pending" and proposal.proposed["aliases"] == [alias]
        assert proposal.proposed["facts"]
        assert session.get(ResearchVerificationJob, identifier).outcome == "review_required"
        assert session.scalar(select(Topic)) is None
    overview = admin_client.get("/v1/admin/overview").json()
    blockers = overview["automation"]["blockers"]
    assert next(group for group in blockers if group["code"] == "evidence_unverified")["count"] == 1


@pytest.mark.integration
@pytest.mark.parametrize("defect", ["kind", "description", "website_url"])
def test_existing_metadata_requires_its_own_semantic_support(
    admin_client, database, pending, monkeypatch, defect
):
    enable(monkeypatch)
    identifier = run_metadata_research(admin_client, pending, monkeypatch, verify=False)

    def unsupported(result):
        next(field for field in result["fields"] if field["field"] == defect)["supported"] = False

    verify_metadata(monkeypatch, identifier, change=unsupported)
    with database() as session:
        assert session.get(TopicProposal, uuid.UUID(pending)).status == "pending"
        assert session.scalar(select(Topic)) is None


@pytest.mark.integration
def test_recovered_identity_citations_do_not_repeat_semantic_research(
    admin_client, database, pending, monkeypatch
):
    enable(monkeypatch)
    identifier = run_metadata_research(admin_client, pending, monkeypatch, verify=False)
    schedule_verification(database)

    def new_source(result):
        result["sources"] = [
            {"url": "https://identity.example/", "quote": "Verified exact identity"}
        ]

    calls = mock_verifier(monkeypatch, change=new_source)

    def unavailable(values):
        result = evidence(values)
        for check in result["checks"].values():
            check.update(status="unverified", reason="transport_error", retryable=True)
        return result

    monkeypatch.setattr(tasks, "verify_citations", unavailable)
    tasks.verify_research(str(identifier))
    with database.begin() as session:
        assert session.get(TopicProposal, uuid.UUID(pending)).status == "pending"
        task = session.get(ResearchVerificationJob, identifier)
        assert task.status == "queued"
        task.available_at = utcnow()
    monkeypatch.setattr(tasks, "verify_citations", evidence)
    tasks.verify_research(str(identifier))
    assert len(calls) == 1
    with database() as session:
        assert session.get(TopicProposal, uuid.UUID(pending)).status == "approved"


@pytest.mark.integration
@pytest.mark.parametrize("uncertain", ["identity", "relevance"])
def test_uncertain_verdict_with_bad_quotes_does_not_prevent_fresh_verification(
    admin_client, database, pending, monkeypatch, uncertain
):
    enable(monkeypatch)
    identifier = run_metadata_research(admin_client, pending, monkeypatch, verify=False)
    schedule_verification(database)

    def bad_quote(result):
        if uncertain == "identity":
            result["verdict"] = "uncertain"
        else:
            result["relevance"]["verdict"] = "uncertain"
        result["sources"] = [{"url": "https://example.com/", "quote": "Invented quotation"}]

    def verify(values):
        result = evidence(values)
        for check in result["checks"].values():
            if check["quote"] == "Invented quotation":
                check.update(status="unverified", reason="quote_not_found")
        return result

    monkeypatch.setattr(tasks, "verify_citations", verify)
    mock_verifier(monkeypatch, change=bad_quote)
    tasks.verify_research(str(identifier))
    with database.begin() as session:
        task = session.get(ResearchVerificationJob, identifier)
        assert task.status == "queued" and task.error == "topic_verification_uncertain"
        task.available_at = utcnow()
    calls = mock_verifier(monkeypatch)
    tasks.verify_research(str(identifier))
    assert len(calls) == 1
    with database() as session:
        assert session.get(TopicProposal, uuid.UUID(pending)).status == "approved"
        job = session.get(TopicAnalysisJob, identifier)
        old_check = job.result["evidence_verification"]["checks"][
            citation_key("https://example.com/", "Invented quotation")
        ]
        assert old_check["status"] == "unverified"  # Kept as history, never blessed.


@pytest.mark.integration
def test_late_topic_verdict_cannot_approve_changed_draft(
    admin_client, database, pending, monkeypatch
):
    enable(monkeypatch)
    identifier = run_metadata_research(admin_client, pending, monkeypatch, verify=False)

    def change():
        with database.begin() as session:
            proposal = session.get(TopicProposal, uuid.UUID(pending))
            proposal.proposed = {**proposal.proposed, "aliases": ["human-edit"]}

    verify_metadata(monkeypatch, identifier, during=change)
    with database() as session:
        assert session.get(TopicProposal, uuid.UUID(pending)).status == "pending"
        assert session.scalar(select(Topic)) is None


@pytest.mark.integration
def test_policy_upgrade_rechecks_completed_legacy_verification_once(
    admin_client, database, pending, monkeypatch
):
    enable(monkeypatch)
    identifier = run_metadata_research(admin_client, pending, monkeypatch, verify=False)
    with database.begin() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        old_check = output(verification_input(proposal))
        del old_check["relevance"]
        job = session.get(TopicAnalysisJob, identifier)
        job.result = {
            **job.result,
            "verification_policy_version": "topic-identity-v1/legacy",
            "topic_verification": {"version": "topic-identity-v1", "check": old_check},
        }
        session.add(
            ResearchVerificationJob(
                id=identifier,
                relationships=False,
                status="succeeded",
                outcome="review_required",
                attempts=1,
                finished_at=utcnow(),
            )
        )
    assert schedule_verification(database) == 1
    assert schedule_verification(database) == 0
    with database() as session:
        job = session.get(TopicAnalysisJob, identifier)
        assert job.result["verification_policy_version"] == POLICY_VERSION
        assert job.result["verification_cycles"][0]["outcome"] == "review_required"
    calls = verify_metadata(monkeypatch, identifier)
    assert len(calls) == 1  # An old supported identity check is never reused for scope.
    with database() as session:
        check = session.get(TopicAnalysisJob, identifier).result["topic_verification"]
        assert check["version"] == VERSION
        assert check["check"]["relevance"]["verdict"] == "in_scope"
    assert schedule_verification(database) == 0


@pytest.mark.integration
def test_topic_correction_keeps_original_approval_and_refuses_stale_replacement(
    admin_client, database, pending, monkeypatch
):
    from devfeed_core.services import OperationConflict
    from devfeed_core.topic_corrections import correct_automatic_topic
    from devfeed_core.topic_proposals import snapshot
    from devfeed_core.topics import TopicWrite

    enable(monkeypatch)
    run_metadata_research(admin_client, pending, monkeypatch)
    with database.begin() as session:
        original = session.get(TopicProposal, uuid.UUID(pending))
        previous = copy.deepcopy(original.applied)
        topic = session.get(Topic, original.topic_id)
        body = TopicWrite.model_validate({**previous["draft"], "aliases": ["Exact name"]})
        with pytest.raises(OperationConflict, match="changed"):
            correct_automatic_topic(
                session,
                topic.id,
                expected_hash="bad",
                body=body,
                note="Repair",
                actor={"subject": "operator"},
            )

        correction = correct_automatic_topic(
            session,
            topic.id,
            expected_hash=snapshot_hash(snapshot(topic)),
            body=body,
            note="Repair",
            actor={"subject": "operator"},
        )
        assert correction.status == "approved" and correction.id != original.id
        assert original.applied == previous
        assert topic.aliases == ["Exact name"]
        with pytest.raises(OperationConflict, match="automatic"):
            correct_automatic_topic(
                session,
                topic.id,
                expected_hash=snapshot_hash(snapshot(topic)),
                body=body,
                note="Repair again",
                actor={"subject": "operator"},
            )


@pytest.mark.integration
def test_audit_repair_dry_run_atomic_rollback_and_idempotence(database):
    import runpy
    from pathlib import Path

    from devfeed_core.services import OperationConflict
    from devfeed_core.topic_auto_approval import ACTOR
    from devfeed_core.topic_proposals import snapshot
    from devfeed_core.topics import TopicWrite, save_topic
    from sqlalchemy import func

    script = runpy.run_path(str(Path(__file__).parents[1] / "scripts/repair_identity_audit.py"))
    with database.begin() as session:
        for name, (field, before, _after, _note) in script["CHANGES"].items():
            body = TopicWrite(name=name, slug=name.lower().replace(" ", "-"), kind="technology")
            setattr(body, field, before)
            topic = save_topic(session, body)
            session.add(
                TopicProposal(
                    batch_id=uuid.uuid4(),
                    topic_id=topic.id,
                    slug=topic.slug,
                    action="create",
                    origin="import",
                    source_name="Fixture",
                    proposed=body.model_dump(mode="json"),
                    created_by={},
                    status="approved",
                    reviewed_by=ACTOR,
                    reviewed_at=utcnow(),
                    applied=snapshot(topic),
                )
            )
    assert len(script["repair"]()["changes"]) == 6
    with database.begin() as session:
        topic = session.scalar(select(Topic).where(Topic.name == "Verilog"))
        topic.aliases = ["human edit"]
    with pytest.raises(OperationConflict, match="changed"):
        script["repair"](apply=True)
    with database.begin() as session:
        assert session.scalar(select(func.count()).select_from(TopicProposal)) == 6
        assert session.scalar(select(Topic).where(Topic.name == "RSS")).aliases == [
            "rfc4287",
            "rfc-4287",
        ]
        session.scalar(select(Topic).where(Topic.name == "Verilog")).aliases = [
            "hdl",
            "hardware-description-language",
        ]
    assert len(script["repair"](apply=True)["changes"]) == 6
    assert script["repair"](apply=True)["changes"] == []
    with database() as session:
        assert session.scalar(select(func.count()).select_from(TopicProposal)) == 12
