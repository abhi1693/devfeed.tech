import logging
import uuid

from devfeed_core import services
from devfeed_core.categories import descendant_ids
from devfeed_core.logging import log_identifier
from devfeed_core.models import ArticleCategory, ArticleTag, Category, Tag
from devfeed_core.schemas import (
    CategoryOut,
    CategoryPatch,
    CategoryTree,
    CategoryWrite,
    TagOut,
    TagPatch,
    TagWrite,
)
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import delete, or_, select

from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page, paginate, prohibit_references, record

router = APIRouter(
    prefix="/v1/admin", tags=["admin-taxonomy"], dependencies=[Depends(require_admin)]
)
logger = logging.getLogger(__name__)


@router.get("/categories", response_model=Page[CategoryOut], operation_id="admin_categories_list")
def categories(
    session: DB,
    query: Listing,
    parent_id: uuid.UUID | None = None,
    topic_id: uuid.UUID | None = None,
):
    statement = select(Category)
    if parent_id:
        statement = statement.where(Category.parent_id == parent_id)
    if topic_id:
        statement = statement.where(Category.topic_id == topic_id)
    if query.q:
        statement = statement.where(
            or_(
                Category.name.icontains(query.q, autoescape=True),
                Category.slug.icontains(query.q, autoescape=True),
            )
        )
    return paginate(session, statement, query, {"name": Category.name, "slug": Category.slug})


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


@router.get(
    "/categories/{category_id}", response_model=CategoryOut, operation_id="admin_category_get"
)
def category_detail(category_id: uuid.UUID, session: DB):
    return record(session, Category, category_id)


@router.post(
    "/categories", response_model=CategoryOut, status_code=201, operation_id="admin_category_create"
)
def create_category(body: CategoryWrite, session: DB):
    category = services.create_category(session, body)
    session.commit()
    logger.info("category_created", extra={"category_id": category.id})
    return category


@router.put(
    "/categories/{category_id}", response_model=CategoryOut, operation_id="admin_category_update"
)
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


@router.get("/tags", response_model=Page[TagOut], operation_id="admin_tags_list")
def tags(
    session: DB,
    query: Listing,
    category: str | None = Query(None, max_length=100),
    category_id: uuid.UUID | None = None,
    topic_id: uuid.UUID | None = None,
):
    statement = select(Tag)
    if category:
        statement = statement.where(Tag.category_id.in_(descendant_ids(category)))
    if category_id:
        statement = statement.where(Tag.category_id == category_id)
    if topic_id:
        statement = statement.where(Tag.topic_id == topic_id)
    if query.q:
        statement = statement.where(
            or_(
                Tag.name.icontains(query.q, autoescape=True),
                Tag.slug.icontains(query.q, autoescape=True),
            )
        )
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


@router.delete("/categories/{category_id}", status_code=204, operation_id="admin_category_delete")
def delete_category(category_id: uuid.UUID, session: DB):
    from devfeed_core.categories import lock_category_tree

    lock_category_tree(session)
    record(session, Category, category_id, lock=True)
    prohibit_references(
        session,
        [
            ("child categories", select(Category).where(Category.parent_id == category_id)),
            ("tags", select(Tag).where(Tag.category_id == category_id)),
            ("articles", select(ArticleCategory).where(ArticleCategory.category_id == category_id)),
        ],
    )
    session.execute(delete(Category).where(Category.id == category_id))
    session.commit()
    logger.info("category_deleted", extra={"category_id": log_identifier(category_id)})
    return Response(status_code=204)


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
