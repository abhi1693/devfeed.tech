"""Moderation queue for identity-free successful search suggestions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict

from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.models import SearchQueryStat
from devfeed_core.schemas import DatabaseText
from devfeed_core.search_suggestions import SearchSuggestionAnalytics, review_query
from devfeed_http.dependencies import run_database
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DBFactory

router = APIRouter(
    prefix="/v1/admin/search-suggestions",
    tags=["admin-search-suggestions"],
    dependencies=[Depends(require_admin)],
)


class SearchSuggestionOut(BaseModel):
    query_hash: str
    query: str
    successful_count: int
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    reviewed_at: datetime | None
    review_note: str | None


class SearchReviewData(TypedDict):
    query_hash: str
    query: str
    status: str
    reviewed_at: datetime | None
    review_note: str | None


class SearchSuggestionReview(BaseModel):
    status: Literal["approved", "rejected"]
    note: DatabaseText | None = Field(default=None, max_length=1000)


@router.get("", response_model=list[SearchSuggestionOut])
async def list_suggestions(
    factory: DBFactory,
    request: Request,
    status: Literal["pending", "approved", "rejected"] | None = None,
    minimum: int = 0,
    limit: int = 100,
) -> list[SearchSuggestionOut]:
    try:
        rows = await SearchSuggestionAnalytics(request.app.state.analytics.client).rank(
            factory, status=status, minimum=minimum, limit=limit
        )
        return [SearchSuggestionOut.model_validate(row) for row in rows]
    except (AnalyticsUnavailable, SQLAlchemyError):
        raise HTTPException(503, "Search analytics are temporarily unavailable") from None


@router.post("/{query_hash}/review", response_model=SearchSuggestionOut)
async def review_suggestion(
    query_hash: str,
    body: SearchSuggestionReview,
    factory: DBFactory,
    admin: Admin,
    request: Request,
) -> SearchSuggestionOut:
    def review(session: Session) -> SearchReviewData:
        value = review_query(
            session, query_hash, status=body.status, reviewer=admin.subject, note=body.note
        )
        session.commit()
        return {
            "query_hash": value.query_hash,
            "query": value.query,
            "status": value.status,
            "reviewed_at": value.reviewed_at,
            "review_note": value.review_note,
        }

    try:
        if len(query_hash) != 64 or any(char not in "0123456789abcdef" for char in query_hash):
            raise ValueError("Invalid query hash")
        exists = await run_database(
            factory, lambda session: session.get(SearchQueryStat, query_hash) is not None
        )
        if not exists:
            raise LookupError("Search query not found")
        metrics = await SearchSuggestionAnalytics(request.app.state.analytics.client).get(
            query_hash
        )
        metadata = await run_database(factory, review)
        return SearchSuggestionOut.model_validate(metadata | metrics)
    except ValueError:
        raise HTTPException(422, "Invalid search suggestion hash") from None
    except LookupError:
        raise HTTPException(404, "Search suggestion not found") from None
    except (AnalyticsUnavailable, SQLAlchemyError):
        raise HTTPException(503, "Search analytics are temporarily unavailable") from None
