import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator import scheduler, topic_analysis_tasks
from devfeed_aggregator.codex_client import AnalysisError
from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.models import Topic, TopicAnalysisJob, TopicProposal, utcnow
from devfeed_core.topic_analysis import TopicResearchResult, apply_topic_research, research_prompt
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
