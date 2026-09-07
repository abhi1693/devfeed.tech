from devfeed_core.models import Tag
from devfeed_core.schemas import (
    TagOut,
)
from fastapi import APIRouter, Query
from sqlalchemy import select

from devfeed_api.cache import CachedReadRoute
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1", tags=["taxonomy"], route_class=CachedReadRoute)


@router.get("/tags", response_model=list[TagOut])
def tags(
    session: DB,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    statement = select(Tag)
    return session.scalars(statement.order_by(Tag.slug).offset(offset).limit(limit)).all()
