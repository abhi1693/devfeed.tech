"""One federated index request plus bounded, authoritative public-record lookups."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from typing import Literal, TypedDict

from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.config import get_settings
from devfeed_core.partner_link_rel import LinkRel, default_link_rel
from devfeed_core.rate_limits import RateLimitBudget, consume_rate_limits
from devfeed_core.schemas import ContentType, DatabaseText, ImageVariant
from devfeed_core.search_clicks import SearchClickTokens
from devfeed_core.search_engine import KINDS, MAX_PAGE, PAGE_SIZE, SearchUnavailable, Typesense
from devfeed_core.search_records import SearchHitData, hit, public_records
from devfeed_core.search_suggestions import (
    SearchSuggestionAnalytics,
    normalize_query,
    query_hash,
    record_successful_query,
)
from devfeed_http.analytics import analytics_client
from devfeed_http.body_limits import RequestBodyPolicy
from devfeed_http.dependencies import run_database
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from devfeed_api.cache import CachedReadRoute, CachePolicy
from devfeed_api.dependencies import DB, DBFactory, get_rate_limit_redis

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
    click_token: str | None = None


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


class SearchClick(BaseModel):
    query: DatabaseText = Field(max_length=200)
    result_kind: Literal["articles", "topics", "sources", "tags"]
    result_id: uuid.UUID
    click_token: str | None = Field(default=None, max_length=80)


def click_tokens() -> SearchClickTokens | None:
    key = get_settings().search_query_key
    return SearchClickTokens(key.get_secret_value()) if key else None


@router.get(
    "/suggestions",
    response_model=list[str],
    openapi_extra=CachePolicy(
        namespace="search-suggestions", revision=1, ttl="content", normalize_query=True
    ).openapi_extra(),
)
async def suggestions(
    factory: DBFactory,
    request: Request,
    q: str = Query("", max_length=200),
    limit: int = Query(5, ge=1, le=10),
) -> list[str]:
    try:
        items = await SearchSuggestionAnalytics(analytics_client(request)).rank(
            factory,
            prefix=q,
            status="approved",
            limit=limit,
            minimum=get_settings().search_suggestion_min_volume,
        )
        return [item["query"] for item in items]
    except ValueError:
        raise HTTPException(422, "Invalid search suggestion prefix") from None
    except (AnalyticsUnavailable, SQLAlchemyError):
        raise HTTPException(503, "Search suggestions are temporarily unavailable") from None


@router.post(
    "/analytics/click",
    status_code=204,
    response_class=Response,
    openapi_extra=RequestBodyPolicy(body_limit=4096).openapi_extra(),
)
async def successful_click(body: SearchClick, factory: DBFactory, request: Request) -> Response:
    headers = {"Cache-Control": "no-store"}
    try:
        query = normalize_query(body.query)
    except ValueError:
        raise HTTPException(422, "Invalid search query", headers=headers) from None
    # A valid receipt is required before an observation is recorded.
    if body.click_token is None:
        return Response(status_code=204, headers=headers)
    tokens = click_tokens()
    if tokens is None or not tokens.valid(
        body.click_token, body.query, body.result_kind, body.result_id
    ):
        raise HTTPException(403, "Invalid search event", headers=headers)

    def persist_metadata(session: Session) -> str:
        digest = query_hash(query)
        prefix = "devfeed:search-click:"
        retry = consume_rate_limits(
            get_rate_limit_redis(),
            [
                RateLimitBudget(f"{prefix}anonymous:minute", 120, 60),
                RateLimitBudget(f"{prefix}anonymous:hour", 1000, 3600),
                RateLimitBudget(f"{prefix}query:{digest}:minute", 20, 60),
                RateLimitBudget(f"{prefix}query:{digest}:hour", 100, 3600),
            ],
        )
        if retry:
            raise HTTPException(
                429, "Search event limit reached", headers={**headers, "Retry-After": str(retry)}
            )
        session.execute(select_timeout(), {"timeout": "500"})
        if body.result_id not in public_records(session, body.result_kind, [body.result_id]):
            raise HTTPException(403, "Invalid search event", headers=headers)
        record_successful_query(session, query)
        session.commit()
        return digest

    try:
        digest = await run_database(factory, persist_metadata)
        await SearchSuggestionAnalytics(analytics_client(request)).record(digest)
    except (RedisError, SQLAlchemyError, AnalyticsUnavailable):
        raise HTTPException(
            503, "Search analytics unavailable", headers={**headers, "Retry-After": "1"}
        ) from None
    return Response(status_code=204, headers=headers)


@router.get(
    "",
    response_model=SearchResponse,
    openapi_extra=CachePolicy(
        namespace="search", revision=1, ttl="content", normalize_query=True
    ).openapi_extra(),
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
        tokens = click_tokens()
        for kind in kinds:
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise SearchUnavailable("Search timed out")
            session.execute(select_timeout(), {"timeout": str(remaining)})
            ids = list(
                dict.fromkeys(uuid.UUID(item["document"]["id"]) for item in matches[kind]["hits"])
            )
            records = public_records(session, kind, ids)
            items: list[SearchHitData] = [
                {
                    **hit(kind, records[value]),
                    "click_token": tokens.issue(query, kind, value) if tokens else None,
                }
                for value in ids
                if value in records
            ]
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
