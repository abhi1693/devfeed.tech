import copy
import uuid
from types import SimpleNamespace

import pytest
from devfeed_aggregator import topic_analysis_tasks
from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.feeds import fetcher
from devfeed_core.models import Topic, TopicAnalysisJob, TopicProposal
from devfeed_core.topic_analysis import TopicResearchResult, apply_topic_research, research_fields
from devfeed_core.topic_branding import VERSION, schedule_topic_branding
from devfeed_core.topic_decision_budget import actionable_topic_backlog, schedule_decisions
from devfeed_core.topic_kinds import TOPIC_KINDS
from devfeed_core.topics import TopicWrite, save_topic
from sqlalchemy import select


@pytest.mark.parametrize("kind", TOPIC_KINDS)
def test_branding_only_adds_missing_urls_for_every_kind(kind):
    draft = TopicWrite(
        name="Example", slug="example", kind=kind, description="Approved description"
    ).model_dump(mode="json")
    proposal = TopicProposal(proposed=draft, status="pending", evidence=[])
    job = TopicAnalysisJob(
        id=uuid.uuid4(),
        input_hash=snapshot_hash(draft),
        input_snapshot={"topic": draft, "branding": True},
    )
    result = branding_result()
    result["kind"] = "tool"
    result["description"] = "An unwanted rewrite"
    before = copy.deepcopy(draft)
    assert apply_topic_research(proposal, job, TopicResearchResult(**result)) == "enriched"
    assert proposal.proposed == {
        **before,
        "logo_url": result["logo_url"],
        "website_url": result["website_url"],
    }
    assert research_fields({"topic": proposal.proposed, "branding": True}) == []


def branding_result():
    return dict(
        outcome="ready",
        kind=None,
        description=None,
        aliases=[],
        keywords=[],
        website_url="https://example.com/",
        logo_url="https://example.com/icon.svg",
        facts=[],
        reasons=[],
        sources=[
            {
                "url": "https://example.com/brand",
                "title": "Example brand",
                "quote": "The official Example icon.",
                "fields": ["website_url", "logo_url"],
            }
        ],
    )


@pytest.mark.parametrize(
    "status,body,mime",
    [(404, b"x", "image/png"), (200, b"", "image/png"), (200, b"html", "text/html")],
)
def test_logo_fetch_rejects_missing_empty_or_non_image(monkeypatch, status, body, mime):
    monkeypatch.setattr(
        fetcher,
        "_fetch",
        lambda *a, **kw: fetcher.FetchResult(status, body, a[0], content_type=mime),
    )
    with pytest.raises(fetcher.FeedError, match="did not return an image"):
        fetcher.fetch_topic_logo("https://example.com/icon")


def test_logo_fetch_uses_bounded_guarded_transport(monkeypatch):
    def fetch(url, *args, **kw):
        assert kw == dict(accept="image/*", max_bytes=2_000_000, timeout=15)
        return fetcher.FetchResult(200, b"svg", url, content_type="image/svg+xml")

    monkeypatch.setattr(fetcher, "_fetch", fetch)
    assert fetcher.fetch_topic_logo("https://example.com/icon.svg").status == 200


def enable(monkeypatch):
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_AUTO_RESEARCH_TOPIC_BRANDING", "true")
    monkeypatch.setenv("DEVFEED_AI_BOUNDED_TOPICS_ENABLED", "true")
    get_settings.cache_clear()


@pytest.mark.integration
def test_scheduler_backfills_all_kinds_once_and_respects_pending_edits(database, monkeypatch):
    enable(monkeypatch)
    with database.begin() as session:
        for name, kind in [
            ("Kubernetes", "platform"),
            ("Python", "language"),
            ("Concept", "concept"),
        ]:
            save_topic(session, TopicWrite(name=name, slug=name.lower(), kind=kind))
    assert schedule_topic_branding(database) == 2
    assert schedule_topic_branding(database) == 0
    with database.begin() as session:
        jobs = session.scalars(select(TopicAnalysisJob)).all()
        assert len(jobs) == 2
        assert all(j.input_snapshot["branding"] for j in jobs)
        for job in jobs:
            job.status, job.outcome = "succeeded", "insufficient_evidence"
        assert not actionable_topic_backlog(session)
    assert schedule_topic_branding(database) == 1
    assert schedule_topic_branding(database) == 0
    with database() as session:
        assert {p.proposed["kind"] for p in session.scalars(select(TopicProposal))} == {
            "platform",
            "language",
            "concept",
        }
        assert all(t.logo_url is None for t in session.scalars(select(Topic)))
    # Identity scheduling must not replace a completed branding attempt.
    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("devfeed_core.topic_decision_budget.topic_admission_limit", lambda _: 8)
    assert schedule_decisions(database, capacity={}) == 0


@pytest.mark.integration
@pytest.mark.parametrize("stale", [False, True])
def test_bounded_worker_preserves_platform_identity_and_requires_review(
    database, monkeypatch, stale
):
    enable(monkeypatch)
    with database.begin() as session:
        topic = save_topic(
            session, TopicWrite(name="Kubernetes", slug="kubernetes", kind="platform")
        )
        topic_id = topic.id
    assert schedule_topic_branding(database) == 1
    with database() as session:
        job_id = session.scalar(select(TopicAnalysisJob.id))
    calls = []
    monkeypatch.setattr(topic_analysis_tasks, "fetch_topic_logo", lambda url: calls.append(url))
    monkeypatch.setattr(topic_analysis_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(
        topic_analysis_tasks,
        "CodexClient",
        lambda _: SimpleNamespace(complete=lambda *a, **kw: branding_result()),
    )
    topic_analysis_tasks._analyze(job_id)
    with database() as session:
        job = session.get(TopicAnalysisJob, job_id)
        proposal = session.get(TopicProposal, job.proposal_id)
        assert job.prompt_version == VERSION
        assert job.status == "succeeded" and job.outcome == "enriched"
        assert proposal.proposed["logo_url"] == "https://example.com/icon.svg"
        assert proposal.proposed["kind"] == "platform"
        assert proposal.status == "pending"
        assert session.get(Topic, topic_id).logo_url is None
    assert calls == ["https://example.com/icon.svg"]
    monkeypatch.setenv("DEVFEED_AUTO_APPROVE_TOPICS", "true")
    get_settings.cache_clear()
    if stale:
        with database.begin() as session:
            session.get(Topic, topic_id).description = "Later operator edit"
    from test_topic_verification import verify_metadata

    verified = verify_metadata(monkeypatch, job_id)
    assert len(verified) == 1 and verified[0]["branding"]
    with database() as session:
        job = session.get(TopicAnalysisJob, job_id)
        proposal = session.get(TopicProposal, job.proposal_id)
        topic = session.get(Topic, topic_id)
        assert proposal.status == ("pending" if stale else "approved")
        assert topic.logo_url == (None if stale else "https://example.com/icon.svg")
        assert topic.kind == "platform"
        if stale:
            assert topic.description == "Later operator edit"
