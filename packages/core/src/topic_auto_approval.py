"""Opt-in application policies use the same review services as administrators."""

import logging
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.models import (
    TopicAnalysisJob,
    TopicProposal,
    TopicRelation,
    TopicRelationProposal,
    utcnow,
)
from devfeed_core.relationship_verification import relationship_verified
from devfeed_core.research_evidence import citation_verified
from devfeed_core.research_verification import metadata_input_current
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topic_proposals import TopicReview, review_proposal
from devfeed_core.topic_relationships import (
    RelationshipReview,
    matching_edges,
    proposal_hash,
    review_relationship,
)
from devfeed_core.topics import lock_topics

logger = logging.getLogger(__name__)
ACTOR = {
    "subject": "taxonomy-auto-approval",
    "issuer": "devfeed",
    "organization_id": "system",
    "name": "Automatic approval",
}


def _review(
    session: Session,
    job: TopicAnalysisJob,
    identifier: uuid.UUID,
    setting: str,
    review: Callable[[str], object],
) -> None:
    entry = {"proposal_id": str(identifier), "setting": setting}
    try:
        # Flush the successful research before this savepoint. An approval conflict
        # must preserve its metadata/evidence and leave it available for manual review.
        with session.begin_nested():
            review(
                f"Automatically approved after successful AI research ({setting}; run {job.id})."
            )
        entry["status"] = "approved"
    except (OperationConflict, RecordNotFound) as exc:
        entry |= {"status": "blocked", "reason": str(exc)}
        logger.warning("taxonomy_auto_approval_blocked", extra={"proposal_id": identifier})
    job.result = {**job.result, "auto_approval": [*job.result.get("auto_approval", []), entry]}


def auto_approve_research(session: Session, job: TopicAnalysisJob) -> None:
    """Review only the proposals made ready by this successful, lease-owned run.

    The worker holds the taxonomy lock. Imports alone and failed/empty research
    cannot authorize approval. Reverification requires the exact inputs to remain current.
    """
    if job.status != "succeeded" or job.outcome != "enriched":
        return
    settings = get_settings()
    if job.proposal_id and settings.auto_approve_topics:
        identifier = job.proposal_id
        proposal = session.get(TopicProposal, identifier)
        if proposal is None or proposal.status != "pending":
            return

        def approve_topic(note: str):
            proposal = session.get(TopicProposal, identifier)
            if proposal is None:
                raise RecordNotFound("Topic proposal not found")
            if not metadata_input_current(job, proposal):
                raise OperationConflict("Topic proposal changed after research")
            sources = job.result.get("sources", [])
            if not sources or not all(
                citation_verified(
                    job.result.get("evidence_verification", {}), source["url"], source["quote"]
                )
                for source in sources
            ):
                raise OperationConflict(
                    "Research citations could not be verified; review the evidence"
                )
            return review_proposal(
                session,
                identifier,
                TopicReview(
                    decision="approved",
                    topic=proposal.proposed,
                    expected_input_hash=snapshot_hash(proposal.proposed),
                    note=note,
                ),
                ACTOR,
            )

        _review(session, job, identifier, "DEVFEED_AUTO_APPROVE_TOPICS", approve_topic)
    elif job.topic_id and settings.auto_approve_topic_relationships:
        if "relationship_verification" not in job.result:
            return  # The scheduler queues an independent review after research.
        for value in job.result.get("proposal_ids", []):
            identifier = uuid.UUID(value)
            relationship_proposal = session.get(TopicRelationProposal, identifier)
            if relationship_proposal is None or relationship_proposal.status != "pending":
                continue

            def approve_relationship(note: str, identifier=identifier):
                proposal = session.get(TopicRelationProposal, identifier)
                if proposal is None or proposal.job_id != job.id:
                    raise RecordNotFound("Relationship proposal for this research run not found")
                if not citation_verified(
                    job.result.get("evidence_verification", {}),
                    proposal.evidence_url,
                    proposal.evidence_quote,
                ):
                    raise OperationConflict(
                        "Research citation could not be verified; review the evidence"
                    )
                if not relationship_verified(
                    job.result.get("relationship_verification", {}), proposal
                ):
                    raise OperationConflict(
                        "Relationship identity and evidence require independent verification"
                    )
                return review_relationship(
                    session,
                    identifier,
                    RelationshipReview(
                        decision="approved", expected_input_hash=proposal_hash(proposal), note=note
                    ),
                    ACTOR,
                )

            _review(
                session,
                job,
                identifier,
                "DEVFEED_AUTO_APPROVE_TOPIC_RELATIONSHIPS",
                approve_relationship,
            )


def retract_automatic_relationship(
    session: Session, identifier: uuid.UUID, *, expected_hash: str, note: str, actor: dict
) -> None:
    """Explicit operator correction, preserving the original automatic review audit."""
    row = session.get(TopicRelationProposal, identifier)
    if row is None:
        raise RecordNotFound("Relationship proposal not found")
    job = session.scalar(
        select(TopicAnalysisJob).where(TopicAnalysisJob.id == row.job_id).with_for_update()
    )
    if job is None:
        raise RecordNotFound("Research run not found")
    lock_topics(session)
    session.refresh(row, with_for_update=True)
    if (
        row.status != "approved"
        or row.reviewed_at is None
        or row.reviewed_by != ACTOR
        or proposal_hash(row) != expected_hash
    ):
        raise OperationConflict("Only the unchanged automatic approval can be corrected")
    edges = session.scalars(
        matching_edges(TopicRelation, row.topic_id, row.related_topic_id, row.relation)
    ).all()
    other_review = session.scalar(
        matching_edges(
            TopicRelationProposal, row.topic_id, row.related_topic_id, row.relation
        ).where(TopicRelationProposal.id != identifier, TopicRelationProposal.status == "approved")
    )
    if len(edges) != 1 or edges[0].evidence_url != row.evidence_url or other_review:
        raise OperationConflict("The edge has another review or changed evidence")
    if not note.strip() or len(note) > 1000:
        raise ValueError("A bounded correction reason is required")
    now = utcnow()
    assert row.reviewed_at is not None
    job.result = {
        **job.result,
        "corrections": [
            *job.result.get("corrections", []),
            {
                "proposal_id": str(identifier),
                "input_hash": expected_hash,
                "corrected_at": now.isoformat(),
                "actor": actor,
                "reason": note,
                "previous_status": row.status,
                "previous_reviewed_at": row.reviewed_at.isoformat(),
                "previous_reviewed_by": row.reviewed_by,
                "previous_review_note": row.review_note,
                "removed_edge": {
                    "topic_id": str(edges[0].topic_id),
                    "related_topic_id": str(edges[0].related_topic_id),
                    "relation": row.relation,
                    "evidence_url": edges[0].evidence_url,
                },
            },
        ],
    }
    session.delete(edges[0])
    row.status, row.reviewed_at, row.reviewed_by, row.review_note = "rejected", now, actor, note
