"""Active-topic relationship discovery and explicit, attributed proposal review."""

import uuid
from typing import Annotated, Literal

from devfeed_core.config import get_settings
from devfeed_core.models import Topic, TopicRelation, TopicRelationProposal
from devfeed_core.schemas import ORMModel
from devfeed_core.services import OperationConflict
from devfeed_core.topic_relationships import (
    RelationKind,
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
from sqlalchemy import UUID, cast, func, literal, or_, select, union_all

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.jobs import AdminJobOut, job_view
from devfeed_admin_api.pagination import Listing, Page, paginate, record
from devfeed_admin_api.search import text_search, topic_search
from devfeed_admin_api.topic_proposals import actor

router = APIRouter(prefix="/v1/admin", tags=["admin-topics"], dependencies=[Depends(require_admin)])


class RelationshipOut(ORMModel):
    topic_id: uuid.UUID
    related_topic_id: uuid.UUID
    topic_name: str
    related_topic_name: str
    relation: RelationKind
    evidence_url: str | None
    status: Literal["approved", "pending"]
    proposal: RelationshipProposalOut | None


@router.get(
    "/topic-relationships",
    response_model=Page[RelationshipOut],
    operation_id="admin_relationships_list",
)
def relationships(
    session: DB,
    query: Listing,
    topic_id: uuid.UUID | None = None,
    status: Literal["approved", "pending"] | None = None,
):
    # Paginate the combined catalog in SQL. Approved proposals are history, not
    # additional edges; only pending proposals join the current relationships.
    rows = union_all(
        select(
            TopicRelation.topic_id,
            TopicRelation.related_topic_id,
            TopicRelation.relation,
            TopicRelation.evidence_url,
            literal("approved").label("status"),
            cast(literal(None), UUID).label("proposal_id"),
        ),
        select(
            TopicRelationProposal.topic_id,
            TopicRelationProposal.related_topic_id,
            TopicRelationProposal.relation,
            TopicRelationProposal.evidence_url,
            TopicRelationProposal.status,
            TopicRelationProposal.id,
        ).where(TopicRelationProposal.status == "pending"),
    ).subquery()
    statement = select(rows)
    if topic_id:
        statement = statement.where(
            or_(rows.c.topic_id == topic_id, rows.c.related_topic_id == topic_id)
        )
    if status:
        statement = statement.where(rows.c.status == status)
    if query.q:
        matches = select(Topic.id).where(topic_search(query.q))
        statement = statement.where(
            or_(
                rows.c.topic_id.in_(matches),
                rows.c.related_topic_id.in_(matches),
                text_search(query.q, rows.c.relation),
            )
        )
    sort = query.sort or "relation"
    column = {"relation": rows.c.relation, "status": rows.c.status}.get(sort.removeprefix("-"))
    if column is None:
        raise HTTPException(422, "Unsupported sort field")
    total = session.scalar(select(func.count()).select_from(statement.subquery()))
    page = (
        session.execute(
            statement.order_by(
                column.desc() if sort.startswith("-") else column.asc(),
                rows.c.topic_id,
                rows.c.related_topic_id,
                rows.c.relation,
                rows.c.status,
                rows.c.proposal_id,
            )
            .offset(query.offset)
            .limit(query.limit)
        )
        .mappings()
        .all()
    )
    identifiers = {row[key] for row in page for key in ("topic_id", "related_topic_id")}
    topics = {
        topic.id: topic for topic in session.scalars(select(Topic).where(Topic.id.in_(identifiers)))
    }
    proposals = {
        proposal.id: proposal_view(proposal, topics)
        for proposal in session.scalars(
            select(TopicRelationProposal).where(
                TopicRelationProposal.id.in_(
                    [row["proposal_id"] for row in page if row["proposal_id"]]
                )
            )
        )
    }
    return {
        "total": total,
        "offset": query.offset,
        "limit": query.limit,
        "items": [
            {
                **row,
                "topic_name": topics[row["topic_id"]].name,
                "related_topic_name": topics[row["related_topic_id"]].name,
                "proposal": proposals.get(row["proposal_id"]),
            }
            for row in page
        ],
    }


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
