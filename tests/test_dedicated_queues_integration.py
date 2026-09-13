"""Queue isolation and upgrade safety with real scheduler, SQL outbox and RQ."""

import uuid
from contextlib import ExitStack
from datetime import timedelta

import pytest
from devfeed_aggregator import scheduler
from devfeed_aggregator.queue import get_queue
from devfeed_core.config import get_settings
from devfeed_core.job_dispatch import dispatch_jobs
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    IngestionJob,
    ResearchVerificationJob,
    Source,
    SourceEnrichmentJob,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
    utcnow,
)
from devfeed_core.worker_queues import QUEUES
from rq.job import JobStatus

pytestmark = pytest.mark.integration


def seed(database):
    now = utcnow()
    with database.begin() as session:
        source = Source(
            name="Publisher",
            feed_url="https://example.com/feed",
            approval_status="approved",
            source_type="publisher",
            enabled=False,
        )
        suggested = Source(
            name="Suggestion",
            feed_url="https://suggested.example/feed",
            approval_status="pending",
            source_type="publisher",
            submitted_by={"user_id": "test"},
            enabled=False,
        )
        article = Article(
            title="Article", canonical_url="https://example.com/article", url_hash="queue-test"
        )
        topic = Topic(name="Topic", slug="topic", kind="technology")
        proposal = TopicProposal(
            batch_id=uuid.uuid4(),
            slug="proposal",
            action="create",
            origin="import",
            source_name="Test",
            proposed={"name": "Proposal"},
            created_by={},
        )
        session.add_all([source, suggested, article, topic, proposal])
        session.flush()
        research = TopicAnalysisJob(
            proposal_id=proposal.id,
            input_hash="a" * 64,
            input_snapshot={},
            requested_by={},
            prompt_version="test",
        )
        relationship = TopicAnalysisJob(
            topic_id=topic.id,
            input_hash="b" * 64,
            input_snapshot={},
            requested_by={},
            prompt_version="test",
        )
        jobs = {
            "ingestion": IngestionJob(source_id=source.id),
            "article-enrichment": ArticleEnrichmentJob(article_id=article.id),
            "images": ArticleImageJob(article_id=article.id),
            "source-enrichment": SourceEnrichmentJob(source_id=source.id),
            "source-analysis": SourceEnrichmentJob(source_id=suggested.id),
            "article-analysis": ArticleAnalysisJob(
                article_id=article.id, available_at=now - timedelta(days=3)
            ),
            "topic-analysis": research,
            "relationships": relationship,
        }
        session.add_all(jobs.values())
        session.flush()
        verification = ResearchVerificationJob(id=research.id, relationships=False)
        session.add(verification)
        session.flush()
        jobs["research-verification"] = verification
        return {name: str(job.id) for name, job in jobs.items()}


def test_scheduler_gives_each_queue_its_own_budget_and_dashboard_matches(
    database, admin_client, monkeypatch
):
    settings = get_settings()
    for name in ("ai_enabled", "full_automation", "auto_approve_topics"):
        monkeypatch.setattr(settings, name, True)
    monkeypatch.setattr(settings, "scheduler_batch_size", 1)
    # Exercise actual dispatch/recovery, with scheduling disabled to retain this exact backlog.
    monkeypatch.setattr(scheduler, "schedule_automation", lambda _: {})
    monkeypatch.setattr(scheduler, "schedule_verification", lambda _: 0)
    expected = seed(database)
    counts = scheduler.tick()
    assert counts["analyses_dispatched"] == 1
    assert counts["topic_analyses_dispatched"] == 2
    assert counts["verifications_dispatched"] == 1
    assert counts["profiles_dispatched"] == 2
    with ExitStack() as stack:
        for name in QUEUES:
            queue = get_queue(name)
            stack.callback(queue.connection.close)
            assert [job.args[0] for job in queue.jobs] == (
                [expected[name]] if name in expected else []
            )
        # Durable totals must use the same partition as scheduler routing.
        response = admin_client.get("/v1/admin/workers")
        assert response.status_code == 200
        queues = {item["name"]: item for item in response.json()["queues"]}
        for name in QUEUES:
            assert queues[name]["queued"] == int(name in expected)
            assert queues[name]["dispatched"] == int(name in expected)
    assert scheduler.tick()["analyses_dispatched"] == 0


@pytest.mark.parametrize("state", [JobStatus.QUEUED, JobStatus.STARTED, JobStatus.FAILED])
def test_redispatch_preserves_legacy_delivery_and_recovers_terminal_delivery(database, state):
    expected = seed(database)
    identifier = uuid.UUID(expected["article-analysis"])
    with ExitStack() as stack:
        legacy, dedicated = get_queue("analysis"), get_queue("article-analysis")
        for queue in (legacy, dedicated):
            stack.callback(queue.connection.close)
        dispatch_jobs(database, legacy, 1, utcnow(), kind="analysis")
        original = legacy.jobs[0]
        if state != JobStatus.QUEUED:
            legacy.remove(original.id)
            original.set_status(state)
        with database.begin() as session:
            session.get(ArticleAnalysisJob, identifier).dispatched_at = utcnow() - timedelta(
                minutes=6
            )
        assert dispatch_jobs(database, dedicated, 1, utcnow(), kind="analysis") == 1
        if state == JobStatus.FAILED:
            assert legacy.count == 0
            assert dedicated.job_ids == [original.id]
            assert dedicated.jobs[0].origin == "article-analysis"
        else:
            assert dedicated.count == 0
            original.refresh()
            assert original.origin == "analysis" and original.get_status() == state
            assert legacy.job_ids == ([original.id] if state == JobStatus.QUEUED else [])
        with database() as session:
            job = session.get(ArticleAnalysisJob, identifier)
            assert job.attempts == 0 and job.lease_token is None


@pytest.mark.parametrize("kind", ["article-enrichment", "images", "source-enrichment"])
def test_immediate_dispatch_uses_the_same_dedicated_queue(database, kind):
    from devfeed_aggregator.dispatch import dispatch_now

    expected = seed(database)
    result = dispatch_now(uuid.UUID(expected[kind]), kind=kind)
    assert result["status"] == "queued"
    with ExitStack() as stack:
        for name in QUEUES:
            queue = get_queue(name)
            stack.callback(queue.connection.close)
            assert [job.args[0] for job in queue.jobs] == ([expected[kind]] if name == kind else [])
