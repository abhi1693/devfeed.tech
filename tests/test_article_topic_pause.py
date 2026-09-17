"""A production pause retains article-derived drafts without stopping imported work."""

import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator import research_verification_tasks, topic_analysis_tasks
from devfeed_core.article_automation import propose_source_topics
from devfeed_core.article_topic_policy import proposal_allowed
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ResearchVerificationJob,
    TopicAnalysisJob,
    TopicProposal,
    utcnow,
)
from devfeed_core.services import OperationConflict
from devfeed_core.topic_analysis import request_all_topic_analysis, request_topic_analysis
from devfeed_core.topic_decision_budget import schedule_decisions
from devfeed_core.topic_proposals import TopicReview, review_proposal
from sqlalchemy import select
from test_topic_analysis import pending as pending


def pause(monkeypatch):
    monkeypatch.setenv("DEVFEED_ARTICLE_TOPIC_PROPOSALS_ENABLED", "false")
    get_settings.cache_clear()


def test_pause_survives_full_automation_and_stops_creation_before_database_access(monkeypatch):
    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
    monkeypatch.setenv("DEVFEED_CODEX_APP_SERVER_URL", "ws://127.0.0.1:4500")
    monkeypatch.setenv("DEVFEED_CODEX_MODEL", "test")
    pause(monkeypatch)
    assert get_settings().auto_approve_topics
    assert not get_settings().article_topic_proposals_enabled
    assert propose_source_topics(object(), Article()) == 0
    assert proposal_allowed(TopicProposal(origin="import"))
    assert not proposal_allowed(TopicProposal(origin="article_enrichment"))
    assert not proposal_allowed(TopicProposal(origin="ai_analysis"))


@pytest.mark.integration
@pytest.mark.parametrize("origin", ["article_enrichment", "ai_analysis"])
def test_pause_retains_queued_jobs_and_blocks_review_without_spending_attempts(
    database, pending, monkeypatch, origin
):
    identifier = uuid.UUID(pending)
    with database.begin() as session:
        proposal = session.get(TopicProposal, identifier)
        proposal.origin = origin
        job = request_topic_analysis(session, identifier, {})
        job_id = job.id
        session.add(ResearchVerificationJob(id=job_id, relationships=False))
    monkeypatch.setenv("DEVFEED_AUTO_APPROVE_TOPICS", "true")
    pause(monkeypatch)
    topic_analysis_tasks._analyze(job_id)
    research_verification_tasks.verify_research(str(job_id))
    with database.begin() as session:
        proposal = session.get(TopicProposal, identifier)
        job = session.get(TopicAnalysisJob, job_id)
        verification = session.get(ResearchVerificationJob, job_id)
        assert proposal.status == "pending"
        assert job.status == verification.status == "queued"
        assert job.attempts == verification.attempts == 0
        assert job.available_at > utcnow()
        assert verification.available_at > utcnow()
        # Approval and new explicit/bulk analysis cannot bypass the pause.
        with pytest.raises(OperationConflict, match="paused"):
            request_topic_analysis(session, identifier, {})
        with pytest.raises(OperationConflict, match="paused"):
            review_proposal(
                session,
                identifier,
                TopicReview(decision="approved", topic=proposal.proposed),
                {},
            )
        assert request_all_topic_analysis(session, {}).queued == 0
    # Resuming preserves the original queued job, rather than creating duplicates.
    monkeypatch.setenv("DEVFEED_ARTICLE_TOPIC_PROPOSALS_ENABLED", "true")
    get_settings.cache_clear()
    with database.begin() as session:
        assert request_topic_analysis(session, identifier, {}).id == job_id


@pytest.mark.integration
@pytest.mark.parametrize("bounded", [False, True])
def test_scheduler_processes_imports_while_article_proposals_are_paused(
    database, pending, monkeypatch, bounded
):
    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
    monkeypatch.setenv("DEVFEED_AI_BOUNDED_TOPICS_ENABLED", str(bounded).lower())
    pause(monkeypatch)
    with database.begin() as session:
        imported = session.get(TopicProposal, uuid.UUID(pending))
        article = TopicProposal(
            batch_id=uuid.uuid4(),
            slug="one-article",
            origin="article_enrichment",
            action="create",
            source_name="Source tag discovery",
            proposed={**imported.proposed, "slug": "one-article"},
            created_by={},
            created_at=utcnow() - timedelta(days=1),
            research_requested=True,
        )
        session.add(article)
        session.flush()
        article_id = article.id
    if bounded:
        assert schedule_decisions(database, capacity={"observed": False}) == 1
    else:
        with database.begin() as session:
            assert request_all_topic_analysis(session, {}).queued == 1
    with database() as session:
        jobs = session.scalars(select(TopicAnalysisJob)).all()
        assert len(jobs) == 1 and jobs[0].proposal_id == uuid.UUID(pending)
        assert session.get(TopicProposal, article_id).status == "pending"
