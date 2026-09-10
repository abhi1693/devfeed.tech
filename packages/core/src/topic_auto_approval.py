"""Opt-in application policies use the same review services as administrators."""

import logging
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.models import TopicAnalysisJob, TopicProposal, TopicRelationProposal
from devfeed_core.research_evidence import citation_verified
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topic_proposals import TopicReview, review_proposal
from devfeed_core.topic_relationships import (
    RelationshipReview,
    proposal_hash,
    review_relationship,
)

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

    The worker holds the taxonomy lock. Imports alone, failed/empty research and
    older proposals are never swept into automatic approval.
    """
    if job.status != "succeeded" or job.outcome != "enriched":
        return
    settings = get_settings()
    if job.proposal_id and settings.auto_approve_topics:
        identifier = job.proposal_id

        def approve_topic(note: str):
            proposal = session.get(TopicProposal, identifier)
            if proposal is None:
                raise RecordNotFound("Topic proposal not found")
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
        for value in job.result.get("proposal_ids", []):
            identifier = uuid.UUID(value)

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
