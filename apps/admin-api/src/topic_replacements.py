"""Replacement choices include inactive topics and unapproved topic proposals."""

import uuid

from devfeed_core.models import Topic, TopicProposal
from devfeed_core.schemas import ORMModel
from fastapi import APIRouter, Depends
from sqlalchemy import UUID, String, cast, func, literal, or_, select, union_all

from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page
from devfeed_admin_api.search import text_search

router = APIRouter(prefix="/v1/admin", tags=["admin-topics"], dependencies=[Depends(require_admin)])


class TopicReplacementOut(ORMModel):
    id: str
    name: str
    slug: str
    kind: str
    status: str
    topic_id: uuid.UUID | None
    proposal_id: uuid.UUID | None


@router.get(
    "/topic-replacements",
    response_model=Page[TopicReplacementOut],
    operation_id="admin_topic_replacements_list",
)
def replacements(session: DB, query: Listing, exclude_topic_id: uuid.UUID | None = None):
    rows = union_all(
        select(
            cast(Topic.id, String).label("id"),
            Topic.name,
            Topic.slug,
            Topic.kind,
            Topic.status,
            Topic.id.label("topic_id"),
            cast(literal(None), UUID).label("proposal_id"),
            func.array_to_string(Topic.aliases, " ").label("aliases"),
        ),
        select(
            literal("proposal:") + cast(TopicProposal.id, String),
            TopicProposal.proposed["name"].astext,
            TopicProposal.slug,
            TopicProposal.proposed["kind"].astext,
            literal("pending"),
            cast(literal(None), UUID),
            TopicProposal.id,
            TopicProposal.proposed["aliases"].astext,
        ).where(
            TopicProposal.status == "pending",
            TopicProposal.topic_id.is_(None),
            TopicProposal.action == "create",
        ),
    ).subquery()
    statement = select(rows)
    if exclude_topic_id:
        statement = statement.where(
            or_(rows.c.topic_id.is_(None), rows.c.topic_id != exclude_topic_id)
        )
    if query.q:
        statement = statement.where(text_search(query.q, rows.c.name, rows.c.slug, rows.c.aliases))
    total = session.scalar(select(func.count()).select_from(statement.subquery()))
    items = (
        session.execute(
            statement.order_by(func.lower(rows.c.name), rows.c.id)
            .offset(query.offset)
            .limit(query.limit)
        )
        .mappings()
        .all()
    )
    return {"items": items, "total": total, "offset": query.offset, "limit": query.limit}
