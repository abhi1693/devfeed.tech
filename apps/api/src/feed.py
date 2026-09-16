import uuid
from typing import Annotated

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.models import Article, ArticleOrigin, ArticleTopic, Source, Tag, Topic
from devfeed_core.publication import visible_article
from devfeed_core.schemas import (
    ArticleOut,
    ContentType,
    FeedOptionsOut,
    FeedPage,
)
from devfeed_http.cursors import decode_cursor as decode_cursor
from devfeed_http.cursors import encode_cursor as encode_cursor
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import distinct, func, literal, literal_column, select, tuple_

from devfeed_api.cache import CachedReadRoute
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1", tags=["discovery"], route_class=CachedReadRoute)


def feed_conditions(
    *,
    q=None,
    tag=None,
    exclude_tag=None,
    source_id=None,
    exclude_source=None,
    content_type=None,
    content_types=None,
    language=None,
    topic=None,
):
    conditions = [visible_article()]
    if topic:
        conditions.append(
            Article.topic_links.any(
                ArticleTopic.role.in_(["primary", "supporting"])
                & ArticleTopic.topic.has((Topic.slug == topic) & (Topic.status == "active"))
            )
        )
    if tag:
        conditions.append(Article.tags.any(Tag.slug.in_(tag)))
    if exclude_tag:
        conditions.append(~Article.tags.any(Tag.slug.in_(exclude_tag)))
    if source_id:
        conditions.append(Article.origins.any(ArticleOrigin.source_id == source_id))
    if exclude_source:
        conditions.append(~Article.origins.any(ArticleOrigin.source_id.in_(exclude_source)))
    if content_type:
        conditions.append(Article.content_type == content_type)
    elif content_types:
        conditions.append(Article.content_type.in_(content_types))
    if language:
        conditions.append(Article.language == language)
    if q:
        vector = func.to_tsvector(
            literal_column("'english'"),
            Article.title + " " + Article.summary + " " + func.coalesce(Article.ai_summary, ""),
        )
        conditions.append(
            vector.op("@@")(func.websearch_to_tsquery(literal_column("'english'"), q))
        )
    return conditions


@router.get("/feed/options", response_model=FeedOptionsOut)
def feed_options(
    session: DB,
    q: str | None = Query(None, min_length=1, max_length=200),
    topic: str | None = Query(None, max_length=100),
    tag: Annotated[list[str] | None, Query(max_length=20)] = None,
    content_type: ContentType | None = None,
    language: str | None = Query(None, pattern=r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", max_length=35),
    source_id: uuid.UUID | None = None,
):
    # Each facet omits only its own filter, so changing a selection stays possible.
    # Fixed query count, no article hydration; the public route cache reuses results.
    common = dict(q=q, topic=topic, tag=tag)
    # Materialize the common visibility/search work once for all three facets.
    # Project only facet keys; never hydrate full article bodies for discovery.
    visible = (
        select(Article.id, Article.content_type, Article.language)
        .where(*feed_conditions(**common))
        .cte("visible_options")
        .prefix_with("MATERIALIZED")
    )

    def facet_conditions(*, kind=False, lang=False, source=False):
        conditions = []
        if kind and content_type:
            conditions.append(visible.c.content_type == content_type)
        if lang and language:
            conditions.append(visible.c.language == language)
        if source and source_id:
            conditions.append(
                select(ArticleOrigin.id)
                .where(
                    ArticleOrigin.article_id == visible.c.id, ArticleOrigin.source_id == source_id
                )
                .exists()
            )
        return conditions

    types_query = select(func.array_agg(distinct(visible.c.content_type))).where(
        *facet_conditions(lang=True, source=True)
    )
    languages_query = select(func.array_agg(distinct(visible.c.language))).where(
        visible.c.language.is_not(None),
        visible.c.language != "",
        *facet_conditions(kind=True, source=True),
    )
    sources_query = (
        select(func.array_agg(distinct(ArticleOrigin.source_id)))
        .join(visible, visible.c.id == ArticleOrigin.article_id)
        .where(*facet_conditions(kind=True, lang=True))
    )
    types, languages, source_ids = session.execute(
        select(
            types_query.scalar_subquery(),
            languages_query.scalar_subquery(),
            sources_query.scalar_subquery(),
        )
    ).one()
    sources = session.scalars(
        select(Source)
        .where(
            Source.enabled.is_(True),
            Source.approval_status == "approved",
            Source.id.in_(source_ids or []),
        )
        .order_by(Source.name, Source.id)
        .limit(500)
    ).all()
    types, languages = sorted(types or []), sorted(languages or [])
    return dict(content_types=types, languages=languages, sources=sources)


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
    content_types: Annotated[list[ContentType] | None, Query(min_length=1, max_length=6)] = None,
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
    statement = (
        select(Article)
        .options(*PUBLIC_ARTICLE_OPTIONS)
        .where(
            *feed_conditions(
                q=q,
                tag=tag,
                exclude_tag=exclude_tag,
                source_id=source_id,
                exclude_source=exclude_source,
                content_type=content_type,
                content_types=content_types,
                language=language,
                topic=topic,
            )
        )
    )
    if position:
        date, identifier = position
        statement = statement.where(
            tuple_(Article.feed_at, Article.id) < tuple_(literal(date), literal(identifier))
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
def article_detail(article_id: str, session: DB):
    try:
        identity = Article.id == uuid.UUID(article_id)
    except ValueError:
        identity = Article.slug == article_id
    article = session.scalar(
        select(Article).options(*PUBLIC_ARTICLE_OPTIONS).where(identity, visible_article())
    )
    if article is None:
        raise HTTPException(404, "Article not found")
    return ArticleOut.from_article(article)
