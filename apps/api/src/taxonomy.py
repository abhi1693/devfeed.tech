import uuid

from devfeed_core.categories import descendant_ids
from devfeed_core.models import Category, Tag
from devfeed_core.schemas import (
    CategoryOut,
    CategoryTree,
    TagOut,
)
from fastapi import APIRouter, Query
from sqlalchemy import select

from devfeed_api.cache import CachedReadRoute
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1", tags=["taxonomy"], route_class=CachedReadRoute)


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
