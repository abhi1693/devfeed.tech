import logging
import uuid

from devfeed_core import services
from devfeed_core.logging import log_identifier
from devfeed_core.models import ArticleTag, Tag
from devfeed_core.schemas import (
    TagOut,
    TagPatch,
    TagWrite,
)
from fastapi import APIRouter, Depends, Response
from sqlalchemy import delete, select

from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page, paginate, prohibit_references, record
from devfeed_admin_api.search import text_search

router = APIRouter(
    prefix="/v1/admin", tags=["admin-taxonomy"], dependencies=[Depends(require_admin)]
)
logger = logging.getLogger(__name__)


@router.get("/tags", response_model=Page[TagOut], operation_id="admin_tags_list")
def tags(
    session: DB,
    query: Listing,
    topic_id: uuid.UUID | None = None,
):
    statement = select(Tag)
    if topic_id:
        statement = statement.where(Tag.topic_id == topic_id)
    if query.q:
        statement = statement.where(text_search(query.q, Tag.name, Tag.slug))
    return paginate(session, statement, query, {"name": Tag.name, "slug": Tag.slug})


@router.get("/tags/{tag_id}", response_model=TagOut, operation_id="admin_tag_get")
def tag_detail(tag_id: uuid.UUID, session: DB):
    return record(session, Tag, tag_id)


@router.post("/tags", response_model=TagOut, status_code=201, operation_id="admin_tag_create")
def create_tag(body: TagWrite, session: DB):
    tag = services.create_tag(session, body)
    session.commit()
    logger.info("tag_created", extra={"tag_id": tag.id})
    return tag


@router.put("/tags/{tag_id}", response_model=TagOut, operation_id="admin_tag_update")
def update_tag(tag_id: uuid.UUID, body: TagWrite, session: DB):
    return save_tag(tag_id, body, session)


@router.patch("/tags/{tag_id}", response_model=TagOut)
def patch_tag(tag_id: uuid.UUID, body: TagPatch, session: DB):
    return save_tag(tag_id, body, session)


def save_tag(tag_id, body: TagWrite | TagPatch, session):
    tag = services.update_tag(session, tag_id, body)
    session.commit()
    logger.info(
        "tag_updated", extra={"tag_id": tag.id, "changed_fields": sorted(body.model_fields_set)}
    )
    return tag


@router.delete("/tags/{tag_id}", status_code=204, operation_id="admin_tag_delete")
def delete_tag(tag_id: uuid.UUID, session: DB):
    record(session, Tag, tag_id, lock=True)
    prohibit_references(
        session, [("articles", select(ArticleTag).where(ArticleTag.tag_id == tag_id))]
    )
    session.execute(delete(Tag).where(Tag.id == tag_id))
    session.commit()
    logger.info("tag_deleted", extra={"tag_id": log_identifier(tag_id)})
    return Response(status_code=204)
