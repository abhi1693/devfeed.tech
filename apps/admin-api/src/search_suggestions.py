"""Moderation queue for identity-free successful search suggestions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from devfeed_core.models import SearchQueryStat
from devfeed_core.schemas import DatabaseText, ORMModel
from devfeed_core.search_suggestions import review_query
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB

router = APIRouter(
    prefix="/v1/admin/search-suggestions",
    tags=["admin-search-suggestions"],
    dependencies=[Depends(require_admin)],
)


class SearchSuggestionOut(ORMModel):
    query_hash: str
    query: str
    status: str
    reviewed_at: datetime | None
    review_note: str | None


class SearchSuggestionReview(BaseModel):
    status: Literal["approved", "rejected"]
    note: DatabaseText | None = Field(default=None, max_length=1000)


@router.get("", response_model=list[SearchSuggestionOut])
def list_suggestions(
    session: DB,
    status: Literal["pending", "approved", "rejected"] | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[SearchSuggestionOut]:
    statement = select(SearchQueryStat)
    if status is not None:
        statement = statement.where(SearchQueryStat.status == status)
    rows = session.scalars(statement.order_by(SearchQueryStat.query).offset(offset).limit(limit))
    return [SearchSuggestionOut.model_validate(row) for row in rows]


@router.post("/{query_hash}/review", response_model=SearchSuggestionOut)
def review_suggestion(
    query_hash: str, body: SearchSuggestionReview, session: DB, admin: Admin
) -> SearchSuggestionOut:
    try:
        row = review_query(
            session, query_hash, status=body.status, reviewer=admin.subject, note=body.note
        )
    except ValueError as exc:
        raise HTTPException(422, "Invalid search suggestion hash") from exc
    except LookupError as exc:
        raise HTTPException(404, "Search suggestion not found") from exc
    result = SearchSuggestionOut.model_validate(row)
    session.commit()
    return result
