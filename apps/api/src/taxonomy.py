from __future__ import annotations

import uuid
from collections.abc import Sequence

from devfeed_core.models import Article, ArticleTag, Tag
from devfeed_core.publication import visible_article
from devfeed_core.schemas import (
    MAX_OFFSET,
    TagPublicOut,
    TextInput,
)
from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from devfeed_api.cache import CachedReadRoute, CachePolicy
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1", tags=["taxonomy"], route_class=CachedReadRoute)


@router.get(
    "/tags",
    response_model=list[TagPublicOut],
    openapi_extra=CachePolicy(namespace="tags", revision=2, ttl="metadata").openapi_extra(),
)
def tags(
    session: DB,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    q: str = Query("", max_length=200),
    letter: str = Query("", pattern=r"^[A-Z#]?$"),
) -> Sequence[Tag]:
    statement = select(Tag).where(
        select(ArticleTag.article_id)
        .join(Article, Article.id == ArticleTag.article_id)
        .where(ArticleTag.tag_id == Tag.id, visible_article())
        .exists()
    )
    if q.strip():
        statement = statement.where(Tag.name.icontains(q.strip(), autoescape=True))
    if letter == "#":
        statement = statement.where(~func.upper(func.substr(Tag.name, 1, 1)).between("A", "Z"))
    elif letter:
        statement = statement.where(func.upper(func.substr(Tag.name, 1, 1)) == letter)
    return session.scalars(
        statement.order_by(
            case((func.upper(func.substr(Tag.name, 1, 1)).between("A", "Z"), 0), else_=1),
            func.lower(Tag.name),
            Tag.slug,
        )
        .offset(offset)
        .limit(limit)
    ).all()


@router.get(
    "/tags/{slug}",
    response_model=TagPublicOut,
    openapi_extra=CachePolicy(namespace="tag", revision=1, ttl="metadata").openapi_extra(),
)
def tag(slug: TextInput, session: DB, by_id: bool = False) -> Tag:
    """Read a visible tag by canonical slug, or by stable UUID when by_id is true."""
    from fastapi import HTTPException

    identity = Tag.slug == slug
    if by_id:
        try:
            identifier = uuid.UUID(slug)
        except ValueError:
            raise HTTPException(404, "Tag not found") from None
        identity = Tag.id == identifier
    result = session.scalar(
        select(Tag).where(
            identity,
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
