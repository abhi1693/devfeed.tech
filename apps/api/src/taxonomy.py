from devfeed_core.models import Article, ArticleTag, Tag
from devfeed_core.publication import visible_article
from devfeed_core.schemas import (
    TagPublicOut,
)
from fastapi import APIRouter, Query
from sqlalchemy import select

from devfeed_api.cache import CachedReadRoute
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1", tags=["taxonomy"], route_class=CachedReadRoute)


@router.get("/tags", response_model=list[TagPublicOut])
def tags(
    session: DB,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    statement = select(Tag).where(
        select(ArticleTag.article_id)
        .join(Article, Article.id == ArticleTag.article_id)
        .where(ArticleTag.tag_id == Tag.id, visible_article())
        .exists()
    )
    return session.scalars(statement.order_by(Tag.slug).offset(offset).limit(limit)).all()


@router.get("/tags/{slug}", response_model=TagPublicOut)
def tag(slug: str, session: DB):
    from fastapi import HTTPException

    result = session.scalar(
        select(Tag).where(
            Tag.slug == slug,
            select(1)
            .select_from(ArticleTag)
            .join(Article, Article.id == ArticleTag.article_id)
            .where(ArticleTag.tag_id == Tag.id, visible_article())
            .exists(),
        )
    )
    if result is None:
        raise HTTPException(404, "Tag not found")
    return result
