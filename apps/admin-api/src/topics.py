"""Topic identity and relationship administration. Linked taxonomy is protected."""

import logging
import uuid
from datetime import datetime
from typing import Literal

from devfeed_core.logging import log_identifier
from devfeed_core.models import (
    ArticleTopic,
    Tag,
    Topic,
    TopicAnalysisJob,
    TopicRelation,
    TopicRelationProposal,
)
from devfeed_core.schemas import ORMModel
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topics import (
    RelationWrite,
    TopicOut,
    TopicWrite,
    lock_topics,
    relate_topics,
    save_topic,
)
from fastapi import APIRouter, Depends, Response
from sqlalchemy import delete, or_, select

from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page, paginate, prohibit_references, record

router = APIRouter(prefix="/v1/admin", tags=["admin-topics"], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)
RelationKind = Literal["uses_language", "depends_on", "implements", "part_of", "related_to"]


class AdminTopicWrite(TopicWrite):
    status: Literal["proposed", "active", "rejected"] = "active"


class AdminTopicOut(TopicOut):
    status: str
    created_at: datetime
    updated_at: datetime


class RelationOut(ORMModel):
    topic_id: uuid.UUID
    related_topic_id: uuid.UUID
    relation: RelationKind
    evidence_url: str | None


@router.get("/topics", response_model=Page[AdminTopicOut], operation_id="admin_topics_list")
def topics(
    session: DB, query: Listing, status: Literal["proposed", "active", "rejected"] | None = None
):
    statement = select(Topic)
    if query.q:
        statement = statement.where(
            or_(
                Topic.name.icontains(query.q, autoescape=True),
                Topic.slug.icontains(query.q, autoescape=True),
            )
        )
    if status:
        statement = statement.where(Topic.status == status)
    return paginate(
        session,
        statement,
        query,
        {
            "name": Topic.name,
            "kind": Topic.kind,
            "status": Topic.status,
            "created_at": Topic.created_at,
        },
    )


@router.get("/topics/{topic_id}", response_model=AdminTopicOut, operation_id="admin_topic_get")
def topic_detail(topic_id: uuid.UUID, session: DB):
    return record(session, Topic, topic_id)


@router.post(
    "/topics", response_model=AdminTopicOut, status_code=201, operation_id="admin_topic_create"
)
def topic_create(body: AdminTopicWrite, session: DB):
    topic = save_topic(session, body)
    session.commit()
    logger.info("topic_created", extra={"topic_id": topic.id})
    return topic


@router.put("/topics/{topic_id}", response_model=AdminTopicOut, operation_id="admin_topic_update")
def topic_update(topic_id: uuid.UUID, body: AdminTopicWrite, session: DB):
    lock_topics(session)
    topic = record(session, Topic, topic_id)
    if topic.status == "active" and body.status != "active":
        prohibit_references(
            session,
            [
                (
                    "article classifications",
                    select(ArticleTopic).where(ArticleTopic.topic_id == topic_id),
                ),
                ("tags", select(Tag).where(Tag.topic_id == topic_id)),
            ],
        )
    topic = save_topic(session, body, topic_id)
    session.commit()
    logger.info("topic_updated", extra={"topic_id": log_identifier(topic_id)})
    return topic


@router.delete("/topics/{topic_id}", status_code=204, operation_id="admin_topic_delete")
def topic_delete(topic_id: uuid.UUID, session: DB):
    lock_topics(session)
    record(session, Topic, topic_id, lock=True)
    prohibit_references(
        session,
        [
            (
                "article classifications",
                select(ArticleTopic).where(ArticleTopic.topic_id == topic_id),
            ),
            ("tags", select(Tag).where(Tag.topic_id == topic_id)),
            (
                "relationship research runs",
                select(TopicAnalysisJob).where(TopicAnalysisJob.topic_id == topic_id),
            ),
            (
                "relationship proposals",
                select(TopicRelationProposal).where(
                    or_(
                        TopicRelationProposal.topic_id == topic_id,
                        TopicRelationProposal.related_topic_id == topic_id,
                    )
                ),
            ),
            (
                "topic relationships",
                select(TopicRelation).where(
                    or_(
                        TopicRelation.topic_id == topic_id,
                        TopicRelation.related_topic_id == topic_id,
                    )
                ),
            ),
        ],
    )
    session.execute(delete(Topic).where(Topic.id == topic_id))
    session.commit()
    logger.info("topic_deleted", extra={"topic_id": log_identifier(topic_id)})
    return Response(status_code=204)


@router.get(
    "/topic-relations", response_model=Page[RelationOut], operation_id="admin_relations_list"
)
def relations(session: DB, query: Listing, topic_id: uuid.UUID | None = None):
    statement = select(TopicRelation)
    if topic_id:
        statement = statement.where(
            or_(TopicRelation.topic_id == topic_id, TopicRelation.related_topic_id == topic_id)
        )
    if query.q:
        matches = select(Topic.id).where(Topic.name.icontains(query.q, autoescape=True))
        statement = statement.where(
            or_(
                TopicRelation.topic_id.in_(matches),
                TopicRelation.related_topic_id.in_(matches),
                TopicRelation.relation.icontains(query.q, autoescape=True),
            )
        )
    return paginate(session, statement, query, {"relation": TopicRelation.relation}, "relation")


def relation_record(session, topic_id, related_topic_id, relation):
    value = session.get(TopicRelation, (topic_id, related_topic_id, relation))
    if value is None:
        raise RecordNotFound("Topic relationship not found")
    return value


@router.get(
    "/topic-relations/{topic_id}/{related_topic_id}/{relation}",
    response_model=RelationOut,
    operation_id="admin_relation_get",
)
def relation_get(
    topic_id: uuid.UUID, related_topic_id: uuid.UUID, relation: RelationKind, session: DB
):
    return relation_record(session, topic_id, related_topic_id, relation)


@router.post(
    "/topic-relations",
    response_model=RelationOut,
    status_code=201,
    operation_id="admin_relation_create",
)
def relation_create(body: RelationWrite, session: DB):
    if session.get(TopicRelation, (body.topic_id, body.related_topic_id, body.relation)):
        raise OperationConflict("This topic relationship already exists")
    value = relate_topics(session, body)
    session.commit()
    logger.info("topic_relationship_created", extra={"topic_id": body.topic_id})
    return value


@router.put(
    "/topic-relations/{topic_id}/{related_topic_id}/{relation}",
    response_model=RelationOut,
    operation_id="admin_relation_update",
)
def relation_update(
    topic_id: uuid.UUID,
    related_topic_id: uuid.UUID,
    relation: RelationKind,
    body: RelationWrite,
    session: DB,
):
    relation_record(session, topic_id, related_topic_id, relation)
    if (topic_id, related_topic_id, relation) != (
        body.topic_id,
        body.related_topic_id,
        body.relation,
    ):
        raise OperationConflict("Relationship identity cannot change; create a new relationship")
    value = relate_topics(session, body)
    session.commit()
    return value


@router.delete(
    "/topic-relations/{topic_id}/{related_topic_id}/{relation}",
    status_code=204,
    operation_id="admin_relation_delete",
)
def relation_delete(
    topic_id: uuid.UUID, related_topic_id: uuid.UUID, relation: RelationKind, session: DB
):
    value = relation_record(session, topic_id, related_topic_id, relation)
    session.delete(value)
    session.commit()
    logger.info("topic_relationship_deleted", extra={"topic_id": log_identifier(topic_id)})
    return Response(status_code=204)
