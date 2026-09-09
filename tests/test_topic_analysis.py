import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from devfeed_aggregator import scheduler, topic_analysis_tasks
from devfeed_aggregator.codex_client import AnalysisError
from devfeed_core import topic_proposals
from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.models import Topic, TopicAnalysisJob, TopicProposal, TopicRelation, utcnow
from devfeed_core.topic_analysis import TopicResearchResult, apply_topic_research, research_prompt
from devfeed_core.topic_proposals import TopicReview, review_proposal, snapshot
from devfeed_core.topic_relationships import (
    RelationshipAnalysisRequest,
    request_relationship_analysis,
)
from devfeed_core.topics import TopicWrite
from sqlalchemy import func, select


def output(**changes):
    return dict(
        outcome="ready",
        description="A precise supported description.",
        aliases=[],
        keywords=["example-specific"],
        website_url="https://example.com/",
        logo_url=None,
        facts=[{"name": "License", "value": "MIT", "source_url": "https://example.com/"}],
        sources=[
            {
                "url": "https://example.com/",
                "title": "Official project",
                "quote": "The project uses MIT licensing.",
                "fields": ["description", "keywords", "website_url", "facts"],
            }
        ],
        reasons=[],
        **changes,
    )


def draft():
    return TopicWrite(
        name="Example", slug="example", kind="technology", description="Human description"
    ).model_dump(mode="json")


def test_research_only_fills_missing_fields_and_records_sources_without_approval():
    proposed = draft()
    proposal = TopicProposal(
        proposed=proposed, status="pending", evidence=[{"provider": "github/explore"}]
    )
    job = TopicAnalysisJob(id=uuid.uuid4(), input_hash=snapshot_hash(proposed), model="model")
    assert (
        apply_topic_research(proposal, job, TopicResearchResult.model_validate(output()))
        == "enriched"
    )
    assert proposal.proposed["description"] == "Human description"
    assert proposal.proposed["keywords"] == ["example-specific"]
    assert proposal.proposed["facts"][0]["retrieved_at"]
    assert proposal.status == "pending" and proposal.topic_id is None
    assert proposal.evidence[0] == {"provider": "github/explore"}
    assert proposal.evidence[-1]["fields"] == ["keywords", "website_url", "facts"]
    assert proposal.evidence[-1]["sources"][0]["url"] == "https://example.com/"
    assert "missing_fields" in research_prompt({"topic": draft(), "evidence": []})


@pytest.mark.parametrize("change", ["status", "draft"])
def test_research_cannot_modify_reviewed_or_changed_proposals(change):
    proposal = TopicProposal(proposed=draft(), status="pending", evidence=[])
    job = TopicAnalysisJob(input_hash=snapshot_hash(proposal.proposed))
    if change == "status":
        proposal.status = "approved"
    else:
        proposal.proposed = {**proposal.proposed, "keywords": ["human"]}
    before = copy.deepcopy(proposal.proposed)
    assert (
        apply_topic_research(proposal, job, TopicResearchResult.model_validate(output()))
        == "superseded"
    )
    assert proposal.proposed == before and proposal.evidence == []


def test_uncited_or_private_metadata_is_rejected():
    proposal = TopicProposal(proposed=draft(), status="pending", evidence=[])
    job = TopicAnalysisJob(input_hash=snapshot_hash(proposal.proposed))
    result = output()
    result["sources"] = []
    with pytest.raises(ValueError, match="source"):
        apply_topic_research(proposal, job, TopicResearchResult.model_validate(result))
    result = output()
    result["website_url"] = "http://127.0.0.1/private"
    with pytest.raises(ValueError):
        TopicResearchResult.model_validate(result)


@pytest.fixture
def pending(database, monkeypatch):
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    get_settings.cache_clear()
    with database.begin() as session:
        proposal = TopicProposal(
            batch_id=uuid.uuid4(),
            slug="example",
            action="create",
            origin="import",
            source_name="Test",
            proposed=draft(),
            evidence=[],
            created_by={"subject": "test"},
        )
        session.add(proposal)
        session.flush()
        return str(proposal.id)


@pytest.mark.integration
def test_ui_request_is_durable_idempotent_attributed_and_never_approves(
    admin_client, database, pending, monkeypatch
):
    route = f"/v1/admin/topic-proposals/{pending}"
    original = admin_client.get(route).json()
    queued = admin_client.post(route + "/analysis")
    assert queued.status_code == 202, queued.text
    job = queued.json()
    assert job["kind"] == "topic-analysis" and job["status"] == "queued"
    assert job["details"]["requested_by"]["subject"] == "integration-admin"
    assert "input_snapshot" not in job["details"]
    assert admin_client.post(route + "/analysis").json()["id"] == job["id"]
    assert admin_client.get(route).json()["analysis"]["id"] == job["id"]
    queue = SimpleNamespace(enqueue=lambda *args, **kw: SimpleNamespace(id="rq-test"))
    assert scheduler.dispatch_jobs(database, queue, 10, utcnow(), topic_analyses=True) == 1
    calls = []

    def complete(prompt, schema, **kwargs):
        calls.append(kwargs)
        return output()

    monkeypatch.setattr(
        topic_analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete)
    )
    topic_analysis_tasks._analyze(uuid.UUID(job["id"]))
    enriched = admin_client.get(route).json()
    assert enriched["status"] == "pending" and enriched["analysis"]["outcome"] == "enriched"
    assert enriched["proposed"]["description"] == original["proposed"]["description"]
    assert enriched["content_hash"] != original["content_hash"]
    assert calls == [{"allow_web_search": True}]
    assert (
        admin_client.get("/v1/admin/topic-proposals").json()["items"][0]["analysis"]["status"]
        == "succeeded"
    )
    assert admin_client.get(f"/v1/admin/jobs/topic-analysis/{job['id']}").status_code == 200
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Topic)) == 0
    # A reviewer who saw the old fields cannot overwrite the enriched version.
    response = admin_client.post(
        route + "/review",
        json={
            "decision": "approved",
            "topic": original["proposed"],
            "expected_input_hash": original["content_hash"],
        },
    )
    assert response.status_code == 409
    response = admin_client.post(
        route + "/review",
        json={
            "decision": "approved",
            "topic": enriched["proposed"],
            "expected_input_hash": enriched["content_hash"],
        },
    )
    assert response.status_code == 200, response.text
    assert admin_client.post(route + "/analysis").status_code == 409


@pytest.mark.integration
@pytest.mark.parametrize("during_inference", [False, True])
def test_worker_cannot_apply_after_rejection(
    admin_client, database, pending, monkeypatch, during_inference
):
    route = f"/v1/admin/topic-proposals/{pending}"
    identifier = uuid.UUID(admin_client.post(route + "/analysis").json()["id"])

    def reject():
        assert (
            admin_client.post(route + "/review", json={"decision": "rejected"}).status_code == 200
        )

    def complete(*args, **kwargs):
        assert during_inference
        reject()
        return output()

    if not during_inference:
        reject()
    monkeypatch.setattr(
        topic_analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete)
    )
    topic_analysis_tasks._analyze(identifier)
    with database() as session:
        job = session.get(TopicAnalysisJob, identifier)
        assert job.outcome == "superseded"
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        assert proposal.status == "rejected" and proposal.proposed == draft()


@pytest.mark.integration
def test_timeout_retries_are_bounded_and_interrupted_jobs_recover(
    admin_client, database, pending, monkeypatch
):
    identifier = uuid.UUID(
        admin_client.post(f"/v1/admin/topic-proposals/{pending}/analysis").json()["id"]
    )

    def complete(*a, **kw):
        raise AnalysisError("codex_timeout")

    monkeypatch.setattr(
        topic_analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete)
    )
    for attempt in range(1, 4):
        topic_analysis_tasks._analyze(identifier)
        with database.begin() as session:
            job = session.get(TopicAnalysisJob, identifier)
            assert job.attempts == attempt and job.error == "codex_timeout"
            assert job.status == ("failed" if attempt == 3 else "queued")
            job.available_at = utcnow() - timedelta(seconds=1)
    with database.begin() as session:
        job = session.get(TopicAnalysisJob, identifier)
        job.status, job.attempts = "running", 1
        job.lease_until = utcnow() - timedelta(seconds=1)
    assert scheduler.recover_analysis_jobs(database, 10, utcnow(), model=TopicAnalysisJob) == 1
    with database() as session:
        assert session.get(TopicAnalysisJob, identifier).status == "queued"


@pytest.mark.integration
def test_disabled_ai_returns_actionable_error_without_creating_jobs(admin_client, database):
    assert (
        admin_client.post(f"/v1/admin/topic-proposals/{uuid.uuid4()}/analysis").status_code == 503
    )
    with database() as session:
        assert session.scalar(select(func.count()).select_from(TopicAnalysisJob)) == 0


@pytest.mark.integration
def test_bulk_queues_every_page_skips_reviewed_complete_and_active_and_is_idempotent(
    admin_client, database, pending
):
    from concurrent.futures import ThreadPoolExecutor

    admin_client.post(f"/v1/admin/topic-proposals/{pending}/analysis").raise_for_status()
    with database.begin() as session:
        for index in range(1300):
            session.add(
                TopicProposal(
                    batch_id=uuid.uuid4(),
                    slug=f"bulk-{index}",
                    action="create",
                    origin="import",
                    source_name="Bulk test",
                    proposed={**draft(), "name": f"Bulk {index}", "slug": f"bulk-{index}"},
                    evidence=[],
                    created_by={"subject": "test"},
                )
            )
        complete = {
            **draft(),
            "slug": "complete",
            "name": "Complete",
            "aliases": ["Alternate"],
            "keywords": ["complete"],
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
        session.add(
            TopicProposal(
                batch_id=uuid.uuid4(),
                slug="complete",
                action="create",
                origin="import",
                source_name="Test",
                proposed=complete,
                evidence=[],
                created_by={"subject": "test"},
            )
        )
        for status in ["approved", "rejected"]:
            session.add(
                TopicProposal(
                    batch_id=uuid.uuid4(),
                    slug=status,
                    action="create",
                    origin="import",
                    source_name="Test",
                    proposed={**draft(), "slug": status},
                    evidence=[],
                    created_by={"subject": "test"},
                    status=status,
                    reviewed_at=utcnow(),
                    reviewed_by={"subject": "reviewer"},
                    applied={"draft": draft()} if status == "approved" else None,
                )
            )
    # Simultaneous clicks by different admins still create one active job per proposal.
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: admin_client.post("/v1/admin/topic-proposals/analysis"), range(2))
        )
    for response in responses:
        assert response.status_code == 202, response.text
        assert response.json()["pending"] == 1302
        assert response.json()["complete"] == 1
    assert sum(response.json()["queued"] for response in responses) == 1300
    repeated = admin_client.post("/v1/admin/topic-proposals/analysis").json()
    assert repeated == {"queued": 0, "already_active": 1301, "complete": 1, "pending": 1302}
    with database() as session:
        jobs = session.scalars(select(TopicAnalysisJob)).all()
        assert len(jobs) == 1301
        assert all(job.requested_by["subject"] == "integration-admin" for job in jobs)
        assert session.scalar(select(func.count()).select_from(Topic)) == 0


@pytest.mark.integration
def test_bulk_disabled_and_empty_cases_do_not_create_jobs(admin_client, database, monkeypatch):
    assert admin_client.post("/v1/admin/topic-proposals/analysis").status_code == 503
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    get_settings.cache_clear()
    result = admin_client.post("/v1/admin/topic-proposals/analysis")
    assert result.status_code == 202
    assert result.json() == {"queued": 0, "already_active": 0, "complete": 0, "pending": 0}


def prepare_relationship_catalog(database, pending, status="active", *, peer=True):
    with database.begin() as session:
        topic = Topic(**draft(), status=status)
        session.add(topic)
        if peer:
            session.add(Topic(name="Peer", slug="peer", kind="technology", status="active"))
        session.add(
            Topic(name="Not reviewed", slug="unreviewed", kind="technology", status="proposed")
        )
        session.flush()
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        proposal.topic_id = topic.id
        if status == "active":
            proposal.action = "update"
            proposal.baseline = snapshot(topic)
        return topic.id


def run_metadata_research(client, pending, monkeypatch, *, result=None, during=None):
    response = client.post(f"/v1/admin/topic-proposals/{pending}/analysis")
    assert response.status_code == 202, response.text
    identifier = uuid.UUID(response.json()["id"])

    def complete(*args, **kwargs):
        if during:
            during()
        return output() if result is None else result

    monkeypatch.setattr(
        topic_analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete)
    )
    topic_analysis_tasks._analyze(identifier)
    return identifier


def approve_enriched(client, pending):
    route = f"/v1/admin/topic-proposals/{pending}"
    proposal = client.get(route).json()
    response = client.post(
        route + "/review",
        json={
            "decision": "approved",
            "topic": proposal["proposed"],
            "expected_input_hash": proposal["content_hash"],
        },
    )
    assert response.status_code == 200, response.text


def relationship_jobs(database):
    with database() as session:
        return session.scalars(
            select(TopicAnalysisJob)
            .where(TopicAnalysisJob.topic_id.is_not(None))
            .order_by(TopicAnalysisJob.created_at, TopicAnalysisJob.id)
        ).all()


@pytest.mark.integration
def test_enrichment_queues_one_attributed_active_only_relationship_run(
    admin_client, database, pending, monkeypatch
):
    topic_id = prepare_relationship_catalog(database, pending)
    identifier = run_metadata_research(admin_client, pending, monkeypatch)
    (followup,) = relationship_jobs(database)
    assert followup.topic_id == topic_id and followup.status == "queued"
    assert followup.requested_by["subject"] == "integration-admin"
    assert {row[2] for row in followup.input_snapshot["catalog"]} == {"example", "peer"}
    with database() as session:
        metadata = session.get(TopicAnalysisJob, identifier)
        assert metadata.status == "succeeded" and metadata.outcome == "enriched"
        assert metadata.result["relationship_analysis_id"] == str(followup.id)
        assert session.get(TopicProposal, uuid.UUID(pending)).status == "pending"
        assert session.get(Topic, topic_id).website_url is None  # Pending fields are not applied.
        assert session.scalar(select(func.count()).select_from(TopicRelation)) == 0
    topic_analysis_tasks._analyze(identifier)  # Re-delivered completed job is a no-op.
    assert len(relationship_jobs(database)) == 1
    queue = SimpleNamespace(enqueue=lambda *args, **kwargs: SimpleNamespace(id="followup-rq"))
    assert scheduler.dispatch_jobs(database, queue, 10, utcnow(), topic_analyses=True) == 1


@pytest.mark.integration
@pytest.mark.parametrize("existing_proposed_topic", [False, True])
def test_new_enriched_topic_queues_relationships_only_after_approval(
    admin_client, database, pending, monkeypatch, existing_proposed_topic
):
    if existing_proposed_topic:
        prepare_relationship_catalog(database, pending, "proposed")
    else:
        with database.begin() as session:
            session.add(Topic(name="Peer", slug="peer", kind="technology", status="active"))
    identifier = run_metadata_research(admin_client, pending, monkeypatch)
    assert relationship_jobs(database) == []
    approve_enriched(admin_client, pending)
    (followup,) = relationship_jobs(database)
    assert followup.status == "queued"
    assert followup.input_snapshot["topic"]["website_url"] == "https://example.com/"
    with database() as session:
        assert session.get(Topic, followup.topic_id).status == "active"
        assert session.get(TopicAnalysisJob, identifier).result["relationship_analysis_id"] == str(
            followup.id
        )


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["timeout", "insufficient_evidence", "inactive"])
def test_followup_skips_failed_inconclusive_or_inactive_topics(
    admin_client, database, pending, monkeypatch, failure
):
    topic_id = prepare_relationship_catalog(database, pending)

    def during():
        if failure == "timeout":
            raise AnalysisError("codex_timeout")
        if failure == "inactive":
            with database.begin() as session:
                session.get(Topic, topic_id).status = "rejected"

    result = output()
    if failure == "insufficient_evidence":
        result["outcome"] = "insufficient_evidence"
    run_metadata_research(admin_client, pending, monkeypatch, result=result, during=during)
    assert relationship_jobs(database) == []
    if failure != "inactive":
        approve_enriched(admin_client, pending)
        assert relationship_jobs(database) == []


@pytest.mark.integration
def test_automatic_followup_reuses_inflight_targeted_manual_research(
    admin_client, database, pending, monkeypatch
):
    topic_id = prepare_relationship_catalog(database, pending)
    with database.begin() as session:
        peer = session.scalar(select(Topic).where(Topic.slug == "peer"))
        manual_id = request_relationship_analysis(
            session,
            topic_id,
            RelationshipAnalysisRequest(related_topic_id=peer.id),
            {"subject": "manual-requester"},
        ).id
    identifier = run_metadata_research(admin_client, pending, monkeypatch)
    (followup,) = relationship_jobs(database)
    assert followup.id == manual_id
    assert followup.requested_by == {"subject": "manual-requester"}
    with database() as session:
        assert session.get(TopicAnalysisJob, identifier).result["relationship_analysis_id"] == str(
            manual_id
        )


@pytest.mark.integration
@pytest.mark.parametrize("approval", ["before_research", "during_research", "after_research"])
def test_approval_resumes_stale_automatic_research_without_duplicate_or_recursive_jobs(
    admin_client, database, pending, monkeypatch, approval
):
    prepare_relationship_catalog(database, pending)
    identifier = run_metadata_research(admin_client, pending, monkeypatch)
    (original,) = relationship_jobs(database)
    if approval == "before_research":
        approve_enriched(admin_client, pending)
    calls = []

    def complete(*args, **kwargs):
        calls.append(True)
        if approval == "during_research":
            approve_enriched(admin_client, pending)
        return {"relationships": [], "reasons": []}

    monkeypatch.setattr(
        topic_analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete)
    )
    topic_analysis_tasks._analyze(original.id)
    if approval == "after_research":
        approve_enriched(admin_client, pending)
    old, replacement = relationship_jobs(database)
    assert old.status == "succeeded"
    assert old.outcome == ("no_additions" if approval == "after_research" else "superseded")
    assert replacement.status == "queued"
    assert replacement.input_snapshot["topic"]["website_url"] == "https://example.com/"
    assert calls == ([] if approval == "before_research" else [True])
    with database() as session:
        assert session.get(TopicAnalysisJob, identifier).result["relationship_analysis_id"] == str(
            replacement.id
        )
    monkeypatch.setattr(
        topic_analysis_tasks,
        "CodexClient",
        lambda _: SimpleNamespace(complete=lambda *a, **kw: {"relationships": [], "reasons": []}),
    )
    topic_analysis_tasks._analyze(original.id)
    topic_analysis_tasks._analyze(replacement.id)
    assert len(relationship_jobs(database)) == 2
    assert relationship_jobs(database)[1].outcome == "no_additions"


@pytest.mark.integration
def test_small_catalog_keeps_enrichment_and_can_queue_on_later_approval(
    admin_client, database, pending, monkeypatch
):
    prepare_relationship_catalog(database, pending, peer=False)
    identifier = run_metadata_research(admin_client, pending, monkeypatch)
    assert relationship_jobs(database) == []
    with database.begin() as session:
        metadata = session.get(TopicAnalysisJob, identifier)
        assert metadata.status == "succeeded" and metadata.outcome == "enriched"
        assert "two active topics" in metadata.result["relationship_analysis_error"]
        session.add(Topic(name="Peer", slug="peer", kind="technology", status="active"))
    approve_enriched(admin_client, pending)
    assert len(relationship_jobs(database)) == 1
    with database() as session:
        assert "relationship_analysis_error" not in session.get(TopicAnalysisJob, identifier).result


@pytest.mark.integration
def test_approval_and_metadata_completion_use_consistent_lock_order(
    admin_client, database, pending, monkeypatch
):
    prepare_relationship_catalog(database, pending)
    response = admin_client.post(f"/v1/admin/topic-proposals/{pending}/analysis")
    identifier = uuid.UUID(response.json()["id"])
    review_locked, worker_waiting = Event(), Event()
    lock = topic_analysis_tasks.lock_topics

    def reviewer_lock(session):
        lock(session)
        review_locked.set()
        assert worker_waiting.wait(5)

    def worker_lock(session):
        worker_waiting.set()
        lock(session)

    def approve():
        with database.begin() as session:
            review_proposal(
                session,
                uuid.UUID(pending),
                TopicReview(decision="approved", topic=draft()),
                {"subject": "reviewer"},
            )

    monkeypatch.setattr(topic_proposals, "lock_topics", reviewer_lock)
    monkeypatch.setattr(topic_analysis_tasks, "lock_topics", worker_lock)
    monkeypatch.setattr(
        topic_analysis_tasks,
        "CodexClient",
        lambda _: SimpleNamespace(complete=lambda *a, **kw: output()),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        review = pool.submit(approve)
        assert review_locked.wait(5)
        research = pool.submit(topic_analysis_tasks._analyze, identifier)
        review.result(timeout=10)
        research.result(timeout=10)
    with database() as session:
        assert session.get(TopicProposal, uuid.UUID(pending)).status == "approved"
        assert session.get(TopicAnalysisJob, identifier).outcome == "superseded"
    assert relationship_jobs(database) == []


@pytest.mark.integration
def test_approval_with_ai_disabled_keeps_topic_without_queueing_research(
    admin_client, database, pending, monkeypatch
):
    prepare_relationship_catalog(database, pending, "proposed")
    run_metadata_research(admin_client, pending, monkeypatch)
    monkeypatch.setattr(get_settings(), "ai_enabled", False)
    approve_enriched(admin_client, pending)
    assert relationship_jobs(database) == []
