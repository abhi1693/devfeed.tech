"""One federated index request plus bounded, authoritative public-record lookups."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from typing import Literal, TypedDict

from devfeed_core.models import Article, ArticleTag, SearchQueryStat, Tag
from devfeed_core.partner_link_rel import LinkRel, default_link_rel
from devfeed_core.publication import visible_article
from devfeed_core.schemas import ContentType, ImageVariant
from devfeed_core.search_engine import KINDS, MAX_PAGE, PAGE_SIZE, SearchUnavailable, Typesense
from devfeed_core.search_records import SearchHitData, hit, public_records
from devfeed_core.search_suggestions import normalize_query
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.elements import TextClause

from devfeed_api.cache import CachedReadRoute, CachePolicy
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1/search", tags=["discovery"], route_class=CachedReadRoute)


class SearchHit(BaseModel):
    sponsor_names: list[str] = Field(default_factory=list)
    sponsor_link_rel: list[LinkRel] = Field(default_factory=default_link_rel)
    logo_variants: list[ImageVariant] = Field(default_factory=list)
    id: uuid.UUID
    title: str
    description: str
    href: str
    image_url: str | None
    label: str
    published_at: str | None


class SearchSection(BaseModel):
    items: list[SearchHit]
    next_cursor: str | None


class SearchResponse(BaseModel):
    query: str
    sections: dict[Literal["articles", "topics", "sources", "tags"], SearchSection]


class SearchSectionData(TypedDict):
    items: list[SearchHitData]
    next_cursor: str | None


class SearchResponseData(TypedDict):
    query: str
    sections: dict[str, SearchSectionData]


@router.get(
    "/suggestions",
    response_model=list[str],
    openapi_extra=CachePolicy(
        namespace="search-suggestions", revision=3, ttl="content"
    ).openapi_extra(),
)
def suggestions(
    session: DB, q: str = Query("", max_length=200), limit: int = Query(5, ge=1, le=10)
) -> list[str]:
    try:
        prefix = normalize_query(q, allow_empty=True)
        if not prefix:
            return []
        statement = (
            select(SearchQueryStat.query)
            .where(
                SearchQueryStat.status == "approved",
                select(Tag.id)
                .join(ArticleTag, ArticleTag.tag_id == Tag.id)
                .join(Article, Article.id == ArticleTag.article_id)
                .where(func.lower(Tag.name) == SearchQueryStat.query, visible_article())
                .exists(),
                SearchQueryStat.query.startswith(prefix, autoescape=True),
            )
            .order_by(SearchQueryStat.query)
            .limit(limit)
        )
        return list(session.scalars(statement))
    except ValueError:
        raise HTTPException(422, "Invalid search suggestion prefix") from None
    except SQLAlchemyError:
        raise HTTPException(503, "Search suggestions are temporarily unavailable") from None


@router.get(
    "",
    response_model=SearchResponse,
    openapi_extra=CachePolicy(namespace="search", revision=2, ttl="content").openapi_extra(),
)
def search(
    session: DB,
    q: str = Query("", max_length=200),
    section: str | None = Query(None, pattern="^(articles|topics|sources|tags)$"),
    page: int = Query(1, ge=1, le=MAX_PAGE),
    sort: Literal["relevance", "newest", "oldest"] = "relevance",
    date_from: date | None = None,
    date_to: date | None = None,
    topics: list[uuid.UUID] | None = Query(None, max_length=20),
    sources: list[uuid.UUID] | None = Query(None, max_length=20),
    tags: list[uuid.UUID] | None = Query(None, max_length=20),
    content_types: list[ContentType] | None = Query(None, max_length=6),
) -> SearchResponseData:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "Start date must not be after end date")
    today = datetime.now(UTC).date()
    if (date_from and date_from > today) or (date_to and date_to > today):
        raise HTTPException(422, "Search dates must not be in the future")
    try:
        query = normalize_query(q, casefold=False, allow_empty=True)
    except ValueError:
        raise HTTPException(422, "Invalid search query") from None
    kinds = (section,) if section else KINDS
    if not any(character.isalnum() for character in query):
        return {
            "query": query,
            "sections": {kind: {"items": [], "next_cursor": None} for kind in kinds},
        }
    deadline = time.monotonic() + 3.0
    try:
        matches = Typesense().search(
            query,
            kinds,
            page,
            sort=sort,
            date_from=int(datetime.combine(date_from, day_time.min, UTC).timestamp())
            if date_from
            else None,
            date_to=int(
                datetime.combine(date_to + timedelta(days=1), day_time.min, UTC).timestamp()
            )
            if date_to
            else None,
            topics=topics,
            sources=sources,
            tags=tags,
            content_types=content_types,
        )
        sections: dict[str, SearchSectionData] = {}
        for kind in kinds:
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise SearchUnavailable("Search timed out")
            session.execute(select_timeout(), {"timeout": str(remaining)})
            ids = list(
                dict.fromkeys(uuid.UUID(item["document"]["id"]) for item in matches[kind]["hits"])
            )
            records = public_records(session, kind, ids)
            items = [hit(kind, records[value]) for value in ids if value in records]
            sections[kind] = {
                "items": items,
                "next_cursor": str(page + 1)
                if page < MAX_PAGE
                and len(matches[kind]["hits"]) == PAGE_SIZE
                and page * PAGE_SIZE < matches[kind].get("found", 0)
                else None,
            }
        return {"query": query, "sections": sections}
    except (SearchUnavailable, SQLAlchemyError, ValueError, KeyError, TypeError) as exc:
        # Never replace an unavailable index with a catalogue-wide DB scan.
        raise HTTPException(503, "Search is temporarily unavailable. Please try again.") from exc


def select_timeout() -> TextClause:
    return text("SELECT set_config('statement_timeout', :timeout, true)")
