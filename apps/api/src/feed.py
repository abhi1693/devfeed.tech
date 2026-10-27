from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated, Literal, TypedDict, TypeVar, get_args

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.models import Article, ArticleOrigin, ArticleTopic, Source, Tag, Topic
from devfeed_core.partner_link_rel import sponsored_article_link_rel
from devfeed_core.publication import visible_article
from devfeed_core.schemas import (
    ArticleOut,
    ContentType,
    FeedOptionsOut,
    FeedPage,
    TextInput,
)
from devfeed_core.user_settings import LanguageCode
from devfeed_http.cursors import decode_cursor as decode_cursor
from devfeed_http.cursors import encode_cursor as encode_cursor
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, literal, literal_column, select, tuple_, union_all
from sqlalchemy.orm import Session, load_only
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import ScalarSelect, Select

from devfeed_api.cache import CachedReadRoute, CachePolicy
from devfeed_api.dependencies import DB
from devfeed_api.latest import FeedFilters

router = APIRouter(prefix="/v1", tags=["discovery"], route_class=CachedReadRoute)


class FacetFilters(TypedDict):
    q: str | None
    topic: str | None
    tag: Sequence[str] | None
    languages: Sequence[str] | None


class FeedOptionsData(TypedDict):
    content_types: list[ContentType]
    sources: Sequence[Source]


Facet = TypeVar("Facet")


def _option_sources(
    *conditions: ColumnElement[bool], article_conditions: list[ColumnElement[bool]] | None = None
) -> Select[Source]:
    # Establish catalog order before testing articles so a nested-loop semijoin
    # can stop after 500 matches. OFFSET 0 preserves this subquery boundary;
    # otherwise the planner can probe the whole catalog before sorting matches.
    candidates = (
        select(Source.id, Source.name)
        .where(Source.enabled.is_(True), Source.approval_status == "approved", *conditions)
        .order_by(Source.name, Source.id)
        .offset(0)
        .subquery()
    )
    selected = select(candidates.c.id, candidates.c.name)
    if article_conditions is not None:
        matching_article = (
            select(literal(1))
            .select_from(ArticleOrigin)
            .join(Article, Article.id == ArticleOrigin.article_id)
            .where(ArticleOrigin.source_id == candidates.c.id, *article_conditions)
            .correlate(candidates)
            .exists()
        )
        selected = selected.where(matching_article)
    limited = selected.order_by(candidates.c.name, candidates.c.id).limit(500).subquery()
    # Hydrate only the limited public records, including managed branding.
    # Editorial receipts and polling state can grow independently of the catalog.
    return (
        select(Source)
        .join(limited, limited.c.id == Source.id)
        .options(
            load_only(
                Source.id,
                Source.slug,
                Source.name,
                Source.source_type,
                Source.description,
                Source.website_url,
                Source.logo_url,
                Source.managed_logo,
                Source.image_url,
                Source.language,
                Source.created_at,
                raiseload=True,
            )
        )
        .order_by(Source.name, Source.id)
    )


def feed_conditions(
    *,
    q: str | None = None,
    tag: Sequence[str] | None = None,
    exclude_tag: Sequence[str] | None = None,
    source_id: uuid.UUID | None = None,
    exclude_source: Sequence[uuid.UUID] | None = None,
    content_type: ContentType | None = None,
    content_types: Sequence[ContentType] | None = None,
    languages: Sequence[str] | None = None,
    topic: str | None = None,
    _approved_origin: bool = False,
) -> list[ColumnElement[bool]]:
    conditions = (
        [Article.publication_status == "published", Article.review_status == "approved"]
        if _approved_origin
        else [visible_article()]
    )
    if topic:
        try:
            topic_identity = Topic.id == uuid.UUID(topic)
        except ValueError:
            topic_identity = Topic.slug == topic
        conditions.append(
            Article.topic_links.any(
                ArticleTopic.role.in_(["primary", "supporting"])
                & ArticleTopic.topic.has(topic_identity & (Topic.status == "active"))
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
    if languages:
        conditions.append(Article.language.in_(languages))
    if q:
        vector = func.to_tsvector(
            literal_column("'english'"),
            Article.title + " " + Article.summary + " " + func.coalesce(Article.ai_summary, ""),
        )
        conditions.append(
            vector.op("@@")(func.websearch_to_tsquery(literal_column("'english'"), q))
        )
    return conditions


@router.get(
    "/feed/options",
    response_model=FeedOptionsOut,
    openapi_extra=CachePolicy(namespace="feed-options", revision=1, ttl="content").openapi_extra(),
)
def feed_options(
    session: DB,
    q: TextInput | None = Query(None, min_length=1, max_length=200),
    topic: TextInput | None = Query(None, max_length=100, description="Active topic slug or UUID"),
    tag: Annotated[list[TextInput] | None, Query(max_length=20)] = None,
    content_type: ContentType | None = None,
    source_id: uuid.UUID | None = None,
    languages: Annotated[list[LanguageCode] | None, Query(min_length=1, max_length=75)] = None,
) -> FeedOptionsData:
    # Sparse topic/tag/search matches should be resolved once, not tested again
    # for every source. Broad browsing benefits from the early-exit probes below.
    if q or topic or tag:
        return _filtered_feed_options(
            session,
            q=q,
            topic=topic,
            tag=tag,
            languages=languages,
            content_type=content_type,
            source_id=source_id,
        )
    # Probe each possible facet with an early exit instead of materializing every
    # visible article. Each facet still omits only its own selected filter.
    common: FacetFilters = dict(q=q, topic=topic, tag=tag, languages=languages)
    # Literal branches let the planner distinguish rare/absent kinds instead of
    # applying an average selectivity to every row of a VALUES table.
    types = session.scalars(
        union_all(
            *(
                select(literal(kind)).where(
                    select(literal(1))
                    .select_from(Article)
                    .where(*feed_conditions(**common, source_id=source_id, content_type=kind))
                    .limit(1)
                    .scalar_subquery()
                    .is_not(None)
                )
                for kind in get_args(ContentType)
            )
        )
    ).all()
    # This facet already joins each origin to its approved outer Source below.
    # Rechecking an approved origin inside visible_article repeats the same proof
    # for every source candidate. Other callers retain the full visibility guard.
    conditions = feed_conditions(**common, content_type=content_type, _approved_origin=True)
    # EXISTS allows PostgreSQL to choose a semijoin for broad matches as well as
    # indexed early-exit probes. A scalar LIMIT subquery forces repeated probes.
    sources = session.scalars(_option_sources(article_conditions=conditions)).all()
    return dict(content_types=sorted(types or []), sources=sources)


def _filtered_feed_options(
    session: Session,
    *,
    q: str | None,
    topic: str | None,
    tag: Sequence[str] | None,
    languages: Sequence[str] | None,
    content_type: ContentType | None,
    source_id: uuid.UUID | None,
) -> FeedOptionsData:
    # Each facet omits only its own filter, so changing a selection stays possible.
    # Fixed query count, no article hydration; the public route cache reuses results.
    common: FacetFilters = dict(q=q, topic=topic, tag=tag, languages=languages)
    # Materialize the common visibility/search work once for both facets.
    # Project only facet keys; never hydrate full article bodies for discovery.
    visible = (
        select(Article.id, Article.content_type)
        .where(*feed_conditions(**common))
        .cte("visible_options")
        .prefix_with("MATERIALIZED")
    )

    def facet_conditions(*, kind: bool = False, source: bool = False) -> list[ColumnElement[bool]]:
        conditions = []
        if kind and content_type:
            conditions.append(visible.c.content_type == content_type)
        if source and source_id:
            conditions.append(
                select(ArticleOrigin.id)
                .where(
                    ArticleOrigin.article_id == visible.c.id, ArticleOrigin.source_id == source_id
                )
                .exists()
            )
        return conditions

    types_query = select(visible.c.content_type).distinct().where(*facet_conditions(source=True))
    sources_query = (
        select(ArticleOrigin.source_id)
        .distinct()
        .join(visible, visible.c.id == ArticleOrigin.article_id)
        .where(*facet_conditions(kind=True))
    )

    # Deduplicate before aggregation: hash a small set instead of sorting every
    # visible article once per facet (tens of thousands of repeated values).
    def facet_values(statement: Select[Facet]) -> ScalarSelect[list[Facet]]:
        rows = statement.subquery()
        return select(func.array_agg(next(iter(rows.c)))).scalar_subquery()

    types, source_ids = session.execute(
        select(
            facet_values(types_query),
            facet_values(sources_query),
        )
    ).one()
    sources = session.scalars(_option_sources(Source.id.in_(source_ids or []))).all()
    return dict(content_types=sorted(types or []), sources=sources)


@router.get(
    "/feed",
    response_model=FeedPage,
    openapi_extra=CachePolicy(namespace="feed", revision=1, ttl="content").openapi_extra(),
)
def feed(
    session: DB,
    limit: int = Query(30, ge=1, le=100),
    diverse: bool = False,
    cursor: str | None = Query(None, max_length=300),
    q: TextInput | None = Query(None, min_length=1, max_length=200),
    tag: Annotated[list[TextInput] | None, Query(max_length=20)] = None,
    exclude_tag: Annotated[list[TextInput] | None, Query(max_length=20)] = None,
    source_id: uuid.UUID | None = None,
    exclude_source: Annotated[list[uuid.UUID] | None, Query(max_length=20)] = None,
    content_type: ContentType | None = None,
    content_types: Annotated[list[ContentType] | None, Query(min_length=1, max_length=6)] = None,
    topic: TextInput | None = Query(None, max_length=100, description="Active topic slug or UUID"),
    languages: Annotated[list[LanguageCode] | None, Query(min_length=1, max_length=75)] = None,
    sort: Literal["newest", "oldest", "most_liked"] = "newest",
) -> FeedPage:
    if sort != "newest":
        from devfeed_api.ordered_feed import ordered_page

        filters: FeedFilters = dict(
            q=q,
            tag=tag,
            exclude_tag=exclude_tag,
            source_id=source_id,
            exclude_source=exclude_source,
            content_type=content_type,
            content_types=content_types,
            languages=languages,
            topic=topic,
        )
        return ordered_page(session, feed_conditions(**filters), filters, sort, limit, cursor)
    if (
        diverse
        and not (q or source_id or topic or tag)
        and (not cursor or cursor.startswith("latest-v1:"))
    ):
        from devfeed_api.latest import latest_page

        filters = dict(
            exclude_tag=exclude_tag,
            exclude_source=exclude_source,
            content_type=content_type,
            content_types=content_types,
            languages=languages,
        )
        return latest_page(session, feed_conditions(**filters), filters, limit, cursor)
    position = decode_cursor(cursor) if cursor else None
    conditions = feed_conditions(
        q=q,
        tag=tag,
        exclude_tag=exclude_tag,
        source_id=source_id,
        exclude_source=exclude_source,
        content_type=content_type,
        content_types=content_types,
        languages=languages,
        topic=topic,
    )
    if position:
        date, identifier = position
        conditions.append(
            tuple_(Article.feed_at, Article.id) < tuple_(literal(date), literal(identifier))
        )
    statement = select(Article).options(*PUBLIC_ARTICLE_OPTIONS)
    if topic or tag or source_id:
        # A small LIMIT can make PostgreSQL scan the entire chronological index
        # looking for sparse associations. Resolve matching keys without that
        # row goal, then page before hydrating article bodies and relationships.
        matching = (
            select(Article.id, Article.feed_at)
            .where(*conditions)
            .cte("filtered_feed")
            .prefix_with("MATERIALIZED")
        )
        page = (
            select(matching.c.id)
            .order_by(matching.c.feed_at.desc(), matching.c.id.desc())
            .limit(limit + 1)
        )
        statement = statement.where(Article.id.in_(page))
    else:
        statement = statement.where(*conditions)
    articles = session.scalars(
        statement.order_by(Article.feed_at.desc(), Article.id.desc()).limit(limit + 1)
    ).all()
    more = len(articles) > limit
    items = articles[:limit]
    return FeedPage(
        items=ArticleOut.from_articles(items),
        next_cursor=encode_cursor(items[-1]) if more else None,
    )


@router.get(
    "/articles/{article_id}",
    response_model=ArticleOut,
    openapi_extra=CachePolicy(namespace="article", revision=1, ttl="content").openapi_extra(),
)
def article_detail(article_id: TextInput, session: DB) -> ArticleOut:
    try:
        identity = Article.id == uuid.UUID(article_id)
    except ValueError:
        identity = Article.slug == article_id
    article = session.scalar(
        select(Article).options(*PUBLIC_ARTICLE_OPTIONS).where(identity, visible_article())
    )
    if article is None:
        raise HTTPException(404, "Article not found")
    policy = (
        sponsored_article_link_rel(session, [article.id]).get(article.id)
        if article.sponsor_names
        else None
    )
    return ArticleOut.from_article(article, sponsor_link_rel=policy)
