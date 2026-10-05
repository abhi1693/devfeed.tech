"""One federated index request plus bounded, authoritative public-record lookups."""

import time
import uuid
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from typing import Literal

from devfeed_core.config import get_settings
from devfeed_core.rate_limits import RateLimitBudget, consume_rate_limits
from devfeed_core.schemas import ContentType, DatabaseText, ImageVariant
from devfeed_core.search_clicks import SearchClickTokens
from devfeed_core.search_engine import KINDS, MAX_PAGE, PAGE_SIZE, SearchUnavailable, Typesense
from devfeed_core.search_records import hit, public_records
from devfeed_core.search_suggestions import (
    approved_suggestions,
    normalize_query,
    query_hash,
    record_successful_query,
)
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from devfeed_api.cache import CachedReadRoute
from devfeed_api.dependencies import DB, get_rate_limit_redis

router = APIRouter(prefix="/v1/search", tags=["discovery"], route_class=CachedReadRoute)


class SearchHit(BaseModel):
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


class SearchClick(BaseModel):
    query: DatabaseText = Field(max_length=200)
    result_kind: Literal["articles", "topics", "sources", "tags"]
    result_id: uuid.UUID
    click_token: str | None = Field(default=None, max_length=80)


def click_tokens() -> SearchClickTokens | None:
    key = get_settings().search_query_key
    return SearchClickTokens(key.get_secret_value()) if key else None


@router.get("/suggestions", response_model=list[str])
def suggestions(
    session: DB,
    q: str = Query("", max_length=200),
    limit: int = Query(5, ge=1, le=10),
):
    try:
        return approved_suggestions(
            session, q, limit=limit, minimum=get_settings().search_suggestion_min_volume
        )
    except ValueError:
        raise HTTPException(422, "Invalid search suggestion prefix") from None


@router.post("/analytics/click", status_code=204, response_class=Response)
def successful_click(body: SearchClick, session: DB):
    headers = {"Cache-Control": "no-store"}
    try:
        query = normalize_query(body.query)
    except ValueError:
        raise HTTPException(422, "Invalid search query", headers=headers) from None
    # Older clients may omit receipts. Keep their best-effort event compatible,
    # but never let an unproven query create an aggregate.
    if body.click_token is None:
        return Response(status_code=204, headers=headers)
    tokens = click_tokens()
    if tokens is None or not tokens.valid(
        body.click_token, body.query, body.result_kind, body.result_id
    ):
        raise HTTPException(403, "Invalid search event", headers=headers)
    try:
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
    except RedisError:
        raise HTTPException(
            503, "Search analytics unavailable", headers={**headers, "Retry-After": "1"}
        ) from None
    if retry:
        raise HTTPException(
            429, "Search event limit reached", headers={**headers, "Retry-After": str(retry)}
        )
    try:
        session.execute(select_timeout(), {"timeout": "500"})
        if body.result_id not in public_records(session, body.result_kind, [body.result_id]):
            raise HTTPException(403, "Invalid search event", headers=headers)
        record_successful_query(session, query)
        session.commit()
    except SQLAlchemyError:
        raise HTTPException(
            503, "Search analytics unavailable", headers={**headers, "Retry-After": "1"}
        ) from None
    return Response(status_code=204, headers=headers)


@router.get("", response_model=SearchResponse)
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
):
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
        sections = {}
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
            items = [
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


def select_timeout():
    return text("SELECT set_config('statement_timeout', :timeout, true)")
