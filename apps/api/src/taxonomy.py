import logging
import uuid

from devfeed_core import services
from devfeed_core.categories import descendant_ids
from devfeed_core.models import Category, Tag
from devfeed_core.schemas import (
    CategoryOut,
    CategoryPatch,
    CategoryTree,
    CategoryWrite,
    TagOut,
    TagPatch,
    TagWrite,
)
from fastapi import APIRouter, Query
from sqlalchemy import select

from devfeed_api.cache import CachedReadRoute
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1", tags=["taxonomy"], route_class=CachedReadRoute)
logger = logging.getLogger(__name__)


@router.get("/categories", response_model=list[CategoryOut])
def categories(
    session: DB,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    parent_id: uuid.UUID | None = None,
):
    statement = select(Category)
    if parent_id:
        statement = statement.where(Category.parent_id == parent_id)
    return session.scalars(
        statement.order_by(Category.name, Category.id).offset(offset).limit(limit)
    ).all()


@router.get("/categories/tree", response_model=list[CategoryTree])
def category_tree(session: DB):
    categories = session.scalars(select(Category).order_by(Category.name, Category.id)).all()
    nodes = {category.id: CategoryTree.model_validate(category) for category in categories}
    roots = []
    for category in categories:
        if category.parent_id is None:
            roots.append(nodes[category.id])
        else:
            nodes[category.parent_id].children.append(nodes[category.id])
    return roots


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(body: CategoryWrite, session: DB):
    category = services.create_category(session, body)
    session.commit()
    logger.info("category_created", extra={"category_id": category.id})
    return category


@router.put("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: uuid.UUID, body: CategoryWrite, session: DB):
    return save_category(category_id, body, session)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
def patch_category(category_id: uuid.UUID, body: CategoryPatch, session: DB):
    return save_category(category_id, body, session)


def save_category(category_id, body: CategoryWrite | CategoryPatch, session):
    category = services.update_category(session, category_id, body)
    session.commit()
    logger.info(
        "category_updated",
        extra={"category_id": category.id, "changed_fields": sorted(body.model_fields_set)},
    )
    return category


@router.get("/tags", response_model=list[TagOut])
def tags(
    session: DB,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    category: str | None = Query(None, max_length=100),
):
    statement = select(Tag)
    if category:
        statement = statement.where(Tag.category_id.in_(descendant_ids(category)))
    return session.scalars(statement.order_by(Tag.slug).offset(offset).limit(limit)).all()


@router.post("/tags", response_model=TagOut, status_code=201)
def create_tag(body: TagWrite, session: DB):
    tag = services.create_tag(session, body)
    session.commit()
    logger.info("tag_created", extra={"tag_id": tag.id})
    return tag


@router.put("/tags/{tag_id}", response_model=TagOut)
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
