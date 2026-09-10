"""Explicit, attributed corrections preserve the original automatic approval."""

import uuid

from sqlalchemy import or_, select

from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import ArticleTopic, Tag, Topic, TopicProposal, TopicRelation, utcnow
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topic_auto_approval import ACTOR
from devfeed_core.topic_proposals import TopicReview, review_proposal, snapshot
from devfeed_core.topics import TopicWrite, lock_topics


def retract_automatic_topic(session, identifier, *, expected_hash, note, actor):
    """Retire an unchanged automatic approval after its links have been reconciled.

    Retain the rejected identity so imports cannot silently recreate it, and retain
    the complete previous review in the proposal's correction evidence.
    """
    if not note.strip() or len(note) > 1000:
        raise ValueError("A bounded correction reason is required")
    lock_topics(session)
    topic = session.scalar(
        select(Topic)
        .where(Topic.id == identifier)
        .with_for_update(nowait=True)
        .execution_options(populate_existing=True)
    )
    if topic is None:
        raise RecordNotFound("Topic not found")
    before = snapshot(topic)
    if topic.status != "active" or snapshot_hash(before) != expected_hash:
        raise OperationConflict("Topic changed before retraction")
    previous = session.scalar(
        select(TopicProposal)
        .where(TopicProposal.topic_id == identifier, TopicProposal.status == "approved")
        .order_by(TopicProposal.reviewed_at.desc(), TopicProposal.id.desc())
        .limit(1)
        .with_for_update(nowait=True)
    )
    if (
        previous is None
        or previous.reviewed_at is None
        or previous.reviewed_by != ACTOR
        or previous.applied != before
    ):
        raise OperationConflict("Only an unchanged automatic approval can be retracted")
    if (
        session.scalar(select(ArticleTopic.article_id).where(ArticleTopic.topic_id == identifier))
        or session.scalar(select(Tag.id).where(Tag.topic_id == identifier))
        or session.scalar(
            select(TopicRelation.topic_id).where(
                or_(
                    TopicRelation.topic_id == identifier,
                    TopicRelation.related_topic_id == identifier,
                )
            )
        )
        or session.scalar(
            select(TopicProposal.id).where(
                TopicProposal.topic_id == identifier, TopicProposal.status == "pending"
            )
        )
    ):
        raise OperationConflict(
            "Reconcile article, tag, relationship and pending proposal links first"
        )
    now = utcnow()
    previous.evidence = [
        *previous.evidence,
        {
            "provider": "authorized_topic_retraction",
            "actor": actor,
            "reason": note,
            "corrected_at": now.isoformat(),
            "previous_status": previous.status,
            "previous_reviewed_at": previous.reviewed_at.isoformat(),
            "previous_reviewed_by": previous.reviewed_by,
            "previous_review_note": previous.review_note,
            "previous_snapshot": before,
        },
    ]
    previous.status, previous.reviewed_at, previous.reviewed_by = "rejected", now, actor
    previous.review_note = note
    topic.status = "rejected"
    from devfeed_core.tag_topic_discovery import catalog_changed

    catalog_changed(session)
    session.flush()


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
