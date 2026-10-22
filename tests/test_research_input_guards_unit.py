"""Research verification preserves human edits, immutable evidence and retry budgets."""

import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core import analysis, research_verification, topic_analysis, topic_relationships
from devfeed_core.analysis import snapshot_hash
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.models import ResearchVerificationJob, TopicAnalysisJob, TopicProposal
from devfeed_core.research_verification import fail_verification, metadata_input_current
from devfeed_core.topic_analysis import TopicResearchResult, apply_topic_research
from test_relationship_policy_unit import researched as researched
from test_topic_analysis import draft, output

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    for module in (analysis, research_verification, topic_analysis):
        monkeypatch.setattr(module, "utcnow", lambda: NOW)


def complete_research(proposal, result):
    job = TopicAnalysisJob(
        id=uuid.uuid4(),
        proposal_id=proposal.id,
        status="running",
        attempts=1,
        created_at=NOW - timedelta(minutes=2),
        available_at=NOW - timedelta(minutes=1),
        lease_token=uuid.uuid4(),
        lease_until=NOW + timedelta(minutes=5),
        input_hash=snapshot_hash(proposal.proposed),
        input_snapshot={
            "topic": deepcopy(proposal.proposed),
            "evidence": deepcopy(proposal.evidence),
        },
        model="test-model",
        prompt_version=topic_analysis.PROMPT_VERSION,
        result=result.model_dump(mode="json"),
        requested_by={"subject": "research-requester"},
    )
    outcome = apply_topic_research(proposal, job, result)
    assert outcome == "enriched"
    finish_job(job, outcome, NOW)
    return job


@pytest.fixture
def completed(clock):
    proposal = TopicProposal(
        id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        slug="example",
        action="create",
        origin="import",
        source_name="Reviewed import",
        proposed=draft(),
        status="pending",
        evidence=[{"provider": "manual_import"}],
        created_at=NOW - timedelta(hours=1),
        created_by={"subject": "operator"},
    )
    job = complete_research(proposal, TopicResearchResult.model_validate(output()))
    return job, proposal


def legacy(job):
    job.result = {key: value for key, value in job.result.items() if key != "applied_input_hash"}


@pytest.mark.parametrize("edited", [False, True])
def test_recorded_full_draft_hash_is_authoritative_and_never_rewritten_on_mismatch(
    completed, edited
):
    job, proposal = completed
    if edited:
        proposal.proposed = {**proposal.proposed, "description": "An operator corrected the scope."}
    before = deepcopy((job.result, job.input_snapshot, proposal.proposed, proposal.evidence))
    assert metadata_input_current(job, proposal) is not edited
    assert (job.result, job.input_snapshot, proposal.proposed, proposal.evidence) == before


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_verification_cannot_reopen_a_reviewed_proposal_even_when_content_hash_matches(
    completed, status
):
    job, proposal = completed
    proposal.status, proposal.reviewed_at, proposal.reviewed_by = status, NOW, {"subject": "editor"}
    proposal.review_note = "Explicit editorial decision."
    if status == "approved":
        proposal.applied = deepcopy(proposal.proposed)
    before = deepcopy(job.result)
    assert not metadata_input_current(job, proposal)
    assert job.result == before and proposal.status == status


def test_legacy_recovery_matches_full_draft_and_patch_without_mutating_source_facts(completed):
    job, proposal = completed
    legacy(job)
    proposal.proposed["facts"][0]["retrieved_at"] = (NOW - timedelta(days=1)).isoformat()
    before = deepcopy((job.result, job.input_snapshot, proposal.proposed, proposal.evidence))
    expected_hash = snapshot_hash(proposal.proposed)
    assert metadata_input_current(job, proposal)
    assert job.result == {**before[0], "applied_input_hash": expected_hash}
    assert (job.input_snapshot, proposal.proposed, proposal.evidence) == before[1:]
    assert proposal.status == "pending" and job.status == "succeeded" and job.outcome == "enriched"


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", "An operator corrected the existing description."),
        ("website_url", "https://example.com/updated-official-site/"),
        ("aliases", ["Human reviewed alias"]),
    ],
)
def test_legacy_patch_receipt_never_blesses_later_human_edits(completed, field, value):
    job, proposal = completed
    legacy(job)
    proposal.proposed = {**proposal.proposed, field: value}
    before = deepcopy((job.result, job.input_snapshot, proposal.proposed, proposal.evidence))
    assert not metadata_input_current(job, proposal)
    assert (job.result, job.input_snapshot, proposal.proposed, proposal.evidence) == before


@pytest.mark.parametrize("receipt", ["missing", "newer-research", "missing-recorded-patch"])
def test_legacy_recovery_requires_the_latest_research_receipt_and_every_recorded_patch_field(
    completed, receipt
):
    job, proposal = completed
    legacy(job)
    if receipt == "missing":
        proposal.evidence = [
            item for item in proposal.evidence if item["provider"] != "ai_topic_research"
        ]
    elif receipt == "newer-research":
        # A second real enrichment supplies the previously missing logo. Its
        # receipt supersedes the old run's authority over this current draft.
        newer = TopicResearchResult.model_validate(
            {
                **output(),
                "description": None,
                "keywords": [],
                "website_url": None,
                "facts": [],
                "logo_url": "https://example.com/logo.png",
                "sources": [
                    {
                        "url": "https://example.com/",
                        "title": "Official project branding",
                        "quote": "Use the official Example project logo.",
                        "fields": ["logo_url"],
                    }
                ],
            }
        )
        complete_research(proposal, newer)
    else:
        # An incomplete historical result cannot prove the receipt's patch.
        del job.result[proposal.evidence[-1]["fields"][0]]
    before = deepcopy((job.result, job.input_snapshot, proposal.proposed, proposal.evidence))
    assert not metadata_input_current(job, proposal)
    assert (job.result, job.input_snapshot, proposal.proposed, proposal.evidence) == before


def verification_job(research, *, relationships=False):
    return ResearchVerificationJob(
        id=research.id,
        relationships=relationships,
        status="running",
        attempts=1,
        created_at=NOW,
        available_at=NOW,
        dispatched_at=NOW,
        lease_token=uuid.uuid4(),
        lease_until=NOW + timedelta(minutes=5),
        usage={"prior_checks": 1},
    )


@pytest.fixture
def verification(clock, completed):
    research, _ = completed
    return verification_job(research)


def test_relationship_budget_deferral_releases_lease_without_spending_the_attempt(
    clock, researched
):
    proposal, topics = researched
    snapshots = {
        str(identifier): topic_relationships.topic_snapshot(value)
        for identifier, value in topics.items()
    }
    snapshot = {
        "topic": snapshots[str(proposal.topic_id)],
        "snapshots": snapshots,
        "catalog": [
            [str(value.id), value.name, value.slug, value.kind] for value in topics.values()
        ],
        "related_topic_id": str(proposal.related_topic_id),
        "excluded_edges": [],
    }
    research = TopicAnalysisJob(
        id=proposal.job_id,
        topic_id=proposal.topic_id,
        status="succeeded",
        outcome="enriched",
        attempts=1,
        created_at=NOW - timedelta(hours=2),
        available_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(minutes=30),
        prompt_version=topic_relationships.PROMPT_VERSION,
        input_snapshot=snapshot,
        input_hash=snapshot_hash(snapshot),
        requested_by=proposal.created_by,
        result={"proposal_ids": [str(proposal.id)]},
    )
    verification = verification_job(research, relationships=True)
    usage = deepcopy(verification.usage)
    fail_verification(verification, "relationship_budget_deferred")
    assert verification.status == "queued" and verification.attempts == 0
    assert verification.available_at == NOW + timedelta(minutes=5)
    assert (
        verification.dispatched_at is verification.lease_token is verification.lease_until is None
    )
    assert verification.finished_at is None and verification.usage == usage


@pytest.mark.parametrize(
    "attempts,retry_after,delay",
    [
        (1, 0, 300),
        (2, 0, 600),
        (1, 1800, 1800),
        (1, 200_000, 86_400),
    ],
)
def test_verification_retry_has_its_own_backoff_and_bounded_publisher_retry_after(
    verification, attempts, retry_after, delay
):
    verification.attempts = attempts
    fail_verification(verification, "citation_transport_error", retry_after=retry_after)
    assert verification.status == "queued" and verification.attempts == attempts
    assert verification.available_at == NOW + timedelta(seconds=delay)
    assert verification.error == "citation_transport_error" and verification.finished_at is None
    assert (
        verification.dispatched_at is verification.lease_token is verification.lease_until is None
    )


def test_provider_capacity_deferrals_do_not_exhaust_normal_verification_retries(verification):
    verification.attempts = 4
    verification.usage = {"capacity_deferrals": 3}
    fail_verification(verification, "citation_transport_error")
    assert verification.status == "queued" and verification.available_at == NOW + timedelta(
        minutes=5
    )
    assert verification.attempts == 4 and verification.usage["capacity_deferrals"] == 3


@pytest.mark.parametrize("reason", ["citation_transport_error", "unexpected_tool_execution"])
def test_exhausted_or_terminal_verification_stops_with_preserved_attempt_history(
    verification, reason
):
    verification.attempts = 3 if reason == "citation_transport_error" else 1
    attempts = verification.attempts
    fail_verification(verification, reason)
    assert verification.status == "failed" and verification.finished_at == NOW
    assert verification.attempts == attempts and verification.error == reason
    assert verification.lease_token is verification.lease_until is None
