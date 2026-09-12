import uuid

from devfeed_core.models import Article, ArticleOrigin, Source
from devfeed_core.publication import visible_article
from devfeed_core.schemas import SourcePublicOut
from devfeed_core.source_types import SourceType
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from devfeed_api.cache import CachedReadRoute
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1/sources", tags=["sources"], route_class=CachedReadRoute)


@router.get("", response_model=list[SourcePublicOut])
def sources(
    session: DB,
    enabled: bool | None = None,
    source_type: SourceType | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    has_articles: bool = Query(
        False, description="Only sources with articles visible in their feed"
    ),
):
    statement = select(Source).where(Source.approval_status == "approved")
    if source_type is not None:
        statement = statement.where(Source.source_type == source_type)
    if enabled is not None:
        statement = statement.where(Source.enabled == enabled)
    if has_articles:
        statement = statement.where(
            select(1)
            .select_from(ArticleOrigin)
            .join(Article, Article.id == ArticleOrigin.article_id)
            .where(ArticleOrigin.source_id == Source.id, visible_article())
            .exists()
        )
    return session.scalars(
        statement.order_by(Source.name, Source.id).offset(offset).limit(limit)
    ).all()


@router.get("/{source_id}", response_model=SourcePublicOut)
def source_detail(source_id: str, session: DB):
    if not 1 <= len(source_id) <= 200:
        raise HTTPException(404, "Source not found")
    try:
        if len(source_id) != 36:
            raise ValueError("Not a UUID route")
        identifier = uuid.UUID(source_id)
    except ValueError:
        source = session.scalar(select(Source).where(Source.slug == source_id))
    else:
        source = session.get(Source, identifier)
    if source is None or source.approval_status != "approved":
        raise HTTPException(404, "Source not found")
    return source
