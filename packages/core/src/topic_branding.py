"""One bounded branding proposal per active topic, independent of identity admission."""

import uuid

from sqlalchemy import func, or_, select

from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.models import ResearchVerificationJob, Topic, TopicAnalysisJob, TopicProposal
from devfeed_core.topics import lock_topics

VERSION = "topic-branding-v1"
ACTOR = {
    "subject": VERSION,
    "issuer": "devfeed",
    "organization_id": "system",
    "name": "Automatic topic branding",
}


def identity_proposal_condition():
    # Branding updates must never enter the minimal identity workflow, which
    # deliberately clears optional metadata, or its automatic correction loop.
    return TopicProposal.source_name != VERSION


def schedule_topic_branding(factory) -> int:
    settings = get_settings()
    if not (settings.ai_enabled and settings.auto_research_topic_branding):
        return 0
    from devfeed_core.topic_proposals import snapshot, topic_draft

    with factory.begin() as session:
        if not session.scalar(select(func.pg_try_advisory_xact_lock(0x444642, 0))):
            return 0
        # Count the gap between research completion and verifier admission too.
        busy = session.scalar(
            select(func.count())
            .select_from(TopicAnalysisJob)
            .join(TopicProposal, TopicProposal.id == TopicAnalysisJob.proposal_id)
            .outerjoin(ResearchVerificationJob, ResearchVerificationJob.id == TopicAnalysisJob.id)
            .where(
                TopicProposal.source_name == VERSION,
                TopicProposal.status == "pending",
                or_(
                    TopicAnalysisJob.status.in_(["queued", "running"]),
                    (TopicAnalysisJob.outcome == "enriched")
                    & or_(
                        ResearchVerificationJob.id.is_(None),
                        ResearchVerificationJob.status.in_(["queued", "running"]),
                    ),
                ),
            )
        )
        slots = min(settings.automation_batch_size, max(0, 2 - (busy or 0)))
        if not slots:
            return 0
        lock_topics(session)
        # Any previous attempt is retained, including not-found and failed work.
        # Polling cannot reset budgets or research the same missing icon forever.
        attempted_or_pending = (
            select(TopicProposal.id)
            .where(
                TopicProposal.topic_id == Topic.id,
                or_(TopicProposal.source_name == VERSION, TopicProposal.status == "pending"),
            )
            .exists()
        )
        topics = session.scalars(
            select(Topic)
            .where(Topic.status == "active", Topic.logo_url.is_(None), ~attempted_or_pending)
            .order_by(Topic.created_at, Topic.id)
            .limit(slots)
        ).all()
        for topic in topics:
            draft = topic_draft(topic).model_dump(mode="json")
            proposal = TopicProposal(
                id=uuid.uuid4(),
                batch_id=uuid.uuid4(),
                topic_id=topic.id,
                slug=topic.slug,
                action="update",
                origin="ai_analysis",
                source_name=VERSION,
                proposed=draft,
                baseline=snapshot(topic),
                evidence=[],
                created_by=ACTOR,
            )
            session.add(proposal)
            session.flush()
            session.add(
                TopicAnalysisJob(
                    proposal_id=proposal.id,
                    input_hash=snapshot_hash(draft),
                    input_snapshot={"topic": draft, "evidence": [], "branding": True},
                    requested_by=ACTOR,
                    prompt_version=VERSION,
                )
            )
        return len(topics)
