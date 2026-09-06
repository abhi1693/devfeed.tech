import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Annotated

from devfeed_core.categories import descendant_ids
from devfeed_core.models import Article, ArticleOrigin, ArticleTopic, Category, Source, Tag, Topic
from devfeed_core.schemas import (
    ArticleOut,
    ContentType,
    FeedPage,
)
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, literal, literal_column, select, tuple_

from devfeed_api.cache import CachedReadRoute
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1", tags=["discovery"], route_class=CachedReadRoute)


def encode_cursor(article: Article) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(
            [
                article.feed_at.isoformat(),
                str(article.id),
            ]
        ).encode()
    ).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        payload = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (
            not isinstance(payload, list)
            or len(payload) != 2
            or not all(isinstance(value, str) for value in payload)
        ):
            raise ValueError("Cursor requires a timestamp and UUID string pair")
        date, identifier = payload
        parsed_date = datetime.fromisoformat(date)
        if parsed_date.tzinfo is None:
            raise ValueError("Cursor requires timezone")
        return parsed_date, uuid.UUID(identifier)
    except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
        raise HTTPException(422, "Invalid feed cursor") from exc


@router.get("/feed", response_model=FeedPage)
def feed(
    session: DB,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=300),
    q: str | None = Query(None, min_length=1, max_length=200),
    category: str | None = Query(None, max_length=100),
    include_descendants: bool = True,
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
    statement = select(Article).where(
        Article.publication_status == "published", Article.review_status == "approved"
    )
    statement = statement.where(
        Article.origins.any(ArticleOrigin.source.has(Source.approval_status == "approved"))
    )
    if topic:
        statement = statement.where(
            Article.topic_links.any(
                (ArticleTopic.role.in_(["primary", "supporting"]))
                & ArticleTopic.topic.has((Topic.slug == topic) & (Topic.status == "active"))
            )
        )
    if cursor:
        date, identifier = decode_cursor(cursor)
        statement = statement.where(
            tuple_(Article.feed_at, Article.id) < tuple_(literal(date), literal(identifier))
        )
    if category:
        category_ids = (
            descendant_ids(category)
            if include_descendants
            else select(Category.id).where(Category.slug == category)
        )
        statement = statement.where(
            Article.categories.any(Category.id.in_(category_ids))
            | Article.tags.any(Tag.category_id.in_(category_ids))
        )
    if tag:
        statement = statement.where(Article.tags.any(Tag.slug.in_(tag)))
    if exclude_tag:
        statement = statement.where(~Article.tags.any(Tag.slug.in_(exclude_tag)))
    if source_id:
        statement = statement.where(Article.origins.any(ArticleOrigin.source_id == source_id))
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
    article = session.get(Article, article_id)
    if (
        article is None
        or article.publication_status != "published"
        or article.review_status != "approved"
        or not any(origin.source.approval_status == "approved" for origin in article.origins)
    ):
        raise HTTPException(404, "Article not found")
    return ArticleOut.from_article(article)
