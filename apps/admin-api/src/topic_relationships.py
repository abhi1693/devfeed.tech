"""Active-topic relationship discovery and explicit, attributed proposal review."""

import uuid
from typing import Annotated, Literal

from devfeed_core.config import get_settings
from devfeed_core.models import Topic, TopicRelationProposal
from devfeed_core.services import OperationConflict
from devfeed_core.topic_relationships import (
    RelationshipAnalysisRequest,
    RelationshipProposalOut,
    RelationshipReview,
    proposal_hash,
    proposal_view,
    request_relationship_analysis,
    review_relationship,
)
from devfeed_core.topics import lock_topics
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import or_, select

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.jobs import AdminJobOut, job_view
from devfeed_admin_api.pagination import Listing, Page, paginate, record
from devfeed_admin_api.search import text_search
from devfeed_admin_api.topic_proposals import actor

router = APIRouter(prefix="/v1/admin", tags=["admin-topics"], dependencies=[Depends(require_admin)])


def views(session, proposals):
    identifiers = {
        identifier
        for proposal in proposals
        for identifier in (proposal.topic_id, proposal.related_topic_id)
    }
    topics = (
        {
            topic.id: topic
            for topic in session.scalars(select(Topic).where(Topic.id.in_(identifiers)))
        }
        if identifiers
        else {}
    )
    return [proposal_view(proposal, topics) for proposal in proposals]


@router.post(
    "/topics/{topic_id}/relationships/analysis",
    response_model=AdminJobOut,
    status_code=202,
    operation_id="admin_topic_relationships_analyze",
)
def analyze(topic_id: uuid.UUID, body: RelationshipAnalysisRequest, session: DB, admin: Admin):
    if not get_settings().ai_enabled:
        raise HTTPException(503, "AI analysis is disabled. Start the AI services first")
    job = request_relationship_analysis(session, topic_id, body, actor(admin))
    session.commit()
    return job_view(job, "topic-analysis")


@router.get(
    "/topic-relationship-proposals",
    response_model=Page[RelationshipProposalOut],
    operation_id="admin_relationship_proposals_list",
)
def listing(
    session: DB,
    query: Listing,
    status: Literal["pending", "approved", "rejected"] | None = None,
    topic_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
):
    statement = select(TopicRelationProposal)
    if status:
        statement = statement.where(TopicRelationProposal.status == status)
    if topic_id:
        statement = statement.where(
            or_(
                TopicRelationProposal.topic_id == topic_id,
                TopicRelationProposal.related_topic_id == topic_id,
            )
        )
    if job_id:
        statement = statement.where(TopicRelationProposal.job_id == job_id)
    if query.q:
        statement = statement.where(
            text_search(
                query.q,
                TopicRelationProposal.topic_snapshot["name"].astext,
                TopicRelationProposal.related_topic_snapshot["name"].astext,
                TopicRelationProposal.topic_snapshot["aliases"].astext,
                TopicRelationProposal.related_topic_snapshot["aliases"].astext,
                TopicRelationProposal.relation,
                TopicRelationProposal.explanation,
            )
        )
    page = paginate(
        session,
        statement,
        query,
        {
            "created_at": TopicRelationProposal.created_at,
            "relation": TopicRelationProposal.relation,
            "status": TopicRelationProposal.status,
        },
        "-created_at",
    )
    page["items"] = views(session, page["items"])
    return page


@router.get(
    "/topic-relationship-proposals/{proposal_id}",
    response_model=RelationshipProposalOut,
    operation_id="admin_relationship_proposal_get",
)
def detail(proposal_id: uuid.UUID, session: DB):
    return views(session, [record(session, TopicRelationProposal, proposal_id)])[0]


@router.post(
    "/topic-relationship-proposals/{proposal_id}/review",
    response_model=RelationshipProposalOut,
    operation_id="admin_relationship_proposal_review",
)
def review(proposal_id: uuid.UUID, body: RelationshipReview, session: DB, admin: Admin):
    value = review_relationship(session, proposal_id, body, actor(admin))
    session.commit()
    return views(session, [value])[0]


@router.delete(
    "/topic-relationship-proposals/{proposal_id}",
    status_code=204,
    operation_id="admin_relationship_proposal_delete",
)
def remove(
    proposal_id: uuid.UUID,
    session: DB,
    expected_input_hash: Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")],
    expected_status: Literal["pending", "approved", "rejected"],
):
    lock_topics(session)
    proposal = record(session, TopicRelationProposal, proposal_id, lock=True)
    if proposal.status != expected_status or proposal_hash(proposal) != expected_input_hash:
        raise OperationConflict("Proposal changed. Reload it before deleting")
    session.delete(proposal)
    session.commit()
    return Response(status_code=204)
