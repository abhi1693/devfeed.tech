import uuid
from typing import Annotated

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.models import Article, ArticleOrigin, ArticleTopic, Tag, Topic
from devfeed_core.publication import visible_article
from devfeed_core.schemas import (
    ArticleOut,
    ContentType,
    FeedPage,
)
from devfeed_http.cursors import decode_cursor as decode_cursor
from devfeed_http.cursors import encode_cursor as encode_cursor
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, literal, literal_column, select, tuple_

from devfeed_api.cache import CachedReadRoute
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1", tags=["discovery"], route_class=CachedReadRoute)


@router.get("/feed", response_model=FeedPage)
def feed(
    session: DB,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=300),
    q: str | None = Query(None, min_length=1, max_length=200),
    tag: Annotated[list[str] | None, Query(max_length=20)] = None,
    exclude_tag: Annotated[list[str] | None, Query(max_length=20)] = None,
    source_id: uuid.UUID | None = None,
    exclude_source: Annotated[list[uuid.UUID] | None, Query(max_length=20)] = None,
    content_type: ContentType | None = None,
    language: str | None = Query(
        None,
        pattern=r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$",
        max_length=35,
        description="Exact article language match. Detection emits base ISO 639-1 codes "
        "such as en or ja, not source languages or regional variants.",
    ),
    topic: str | None = Query(None, max_length=100),
):
    position = decode_cursor(cursor) if cursor else None
    statement = select(Article).options(*PUBLIC_ARTICLE_OPTIONS).where(visible_article())
    if topic:
        topic_id = session.scalar(
            select(Topic.id).where(Topic.slug == topic, Topic.status == "active")
        )
        if topic_id is None:
            return FeedPage(items=[], next_cursor=None)
        statement = statement.where(
            Article.topic_links.any(
                (ArticleTopic.topic_id == topic_id)
                & ArticleTopic.role.in_(["primary", "supporting"])
                & ArticleTopic.topic.has(Topic.status == "active")
            )
        )
    if position:
        date, identifier = position
        statement = statement.where(
            tuple_(Article.feed_at, Article.id) < tuple_(literal(date), literal(identifier))
        )
    if tag:
        statement = statement.where(Article.tags.any(Tag.slug.in_(tag)))
    if exclude_tag:
        statement = statement.where(~Article.tags.any(Tag.slug.in_(exclude_tag)))
    if source_id:
        statement = statement.where(
            Article.id.in_(
                select(ArticleOrigin.article_id)
                .where(ArticleOrigin.source_id == source_id)
                .distinct()
            )
        )
    if exclude_source:
        statement = statement.where(
            ~Article.origins.any(ArticleOrigin.source_id.in_(exclude_source))
        )
    if content_type:
        statement = statement.where(Article.content_type == content_type)
    if language:
        statement = statement.where(Article.language == language)
    if q:
        vector = func.to_tsvector(
            literal_column("'english'"),
            Article.title + " " + Article.summary + " " + func.coalesce(Article.ai_summary, ""),
        )
        statement = statement.where(
            vector.op("@@")(func.websearch_to_tsquery(literal_column("'english'"), q))
        )
    articles = session.scalars(
        statement.order_by(Article.feed_at.desc(), Article.id.desc()).limit(limit + 1)
    ).all()
    more = len(articles) > limit
    items = articles[:limit]
    return FeedPage(
        items=[ArticleOut.from_article(article) for article in items],
        next_cursor=encode_cursor(items[-1]) if more else None,
    )


@router.get("/articles/{article_id}", response_model=ArticleOut)
def article_detail(article_id: uuid.UUID, session: DB):
    article = session.scalar(
        select(Article)
        .options(*PUBLIC_ARTICLE_OPTIONS)
        .where(Article.id == article_id, visible_article())
    )
    if article is None:
        raise HTTPException(404, "Article not found")
    return ArticleOut.from_article(article)
