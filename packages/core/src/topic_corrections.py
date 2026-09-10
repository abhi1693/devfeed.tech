"""Explicit, attributed corrections preserve the original automatic approval."""

import uuid

from sqlalchemy import select

from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import Topic, TopicProposal
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topic_auto_approval import ACTOR
from devfeed_core.topic_proposals import TopicReview, review_proposal, snapshot
from devfeed_core.topics import TopicWrite, lock_topics


def correct_automatic_topic(session, identifier, *, expected_hash, body: TopicWrite, note, actor):
    """Caller supplies an explicitly reviewed replacement and expected current snapshot."""
    if not note.strip() or len(note) > 1000:
        raise ValueError("A bounded correction reason is required")
    lock_topics(session)
    topic = session.scalar(
        select(Topic).where(Topic.id == identifier).execution_options(populate_existing=True)
    )
    if topic is None:
        raise RecordNotFound("Topic not found")
    before = snapshot(topic)
    if snapshot_hash(before) != expected_hash:
        raise OperationConflict("Topic changed before correction")
    previous = session.scalar(
        select(TopicProposal)
        .where(TopicProposal.topic_id == identifier, TopicProposal.status == "approved")
        .order_by(TopicProposal.reviewed_at.desc(), TopicProposal.id.desc())
        .limit(1)
    )
    if previous is None or previous.reviewed_by != ACTOR or previous.applied != before:
        raise OperationConflict("Only an unchanged automatic approval can be corrected")
    if body.slug != topic.slug or body.name != topic.name:
        raise OperationConflict("Identity corrections cannot replace the named entity")
    if session.scalar(
        select(TopicProposal.id).where(
            TopicProposal.topic_id == identifier, TopicProposal.status == "pending"
        )
    ):
        raise OperationConflict("A pending proposal already reviews this topic")
    proposal = TopicProposal(
        batch_id=uuid.uuid4(),
        topic_id=identifier,
        slug=topic.slug,
        action="update",
        origin="import",
        source_name="Authorized identity correction",
        proposed=body.model_dump(mode="json"),
        baseline=before,
        created_by=actor,
        research_requested=False,
        evidence=[
            {
                "provider": "authorized_identity_correction",
                "previous_proposal_id": str(previous.id),
                "reason": note,
            }
        ],
    )
    session.add(proposal)
    session.flush()
    return review_proposal(
        session,
        proposal.id,
        TopicReview(
            decision="approved",
            topic=body.model_dump(mode="json"),
            expected_input_hash=snapshot_hash(proposal.proposed),
            note=note,
        ),
        actor,
    )
