"""Moderation queue for identity-free successful search suggestions."""

from datetime import datetime
from typing import Literal

from devfeed_core.models import SearchQueryStat
from devfeed_core.search_suggestions import review_query
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB

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

    @classmethod
    def from_model(cls, value: SearchQueryStat):
        return cls.model_validate(value, from_attributes=True)


class SearchSuggestionReview(BaseModel):
    status: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=1000)


@router.get("", response_model=list[SearchSuggestionOut])
def list_suggestions(
    session: DB,
    status: Literal["pending", "approved", "rejected"] | None = None,
    minimum: int = 0,
    limit: int = 100,
):
    statement = select(SearchQueryStat).order_by(
        SearchQueryStat.successful_count.desc(), SearchQueryStat.query
    )
    if status:
        statement = statement.where(SearchQueryStat.status == status)
    statement = statement.where(SearchQueryStat.successful_count >= max(0, minimum)).limit(
        min(max(limit, 1), 500)
    )
    return [SearchSuggestionOut.from_model(item) for item in session.scalars(statement)]


@router.post("/{query_hash}/review", response_model=SearchSuggestionOut)
def review_suggestion(query_hash: str, body: SearchSuggestionReview, session: DB, admin: Admin):
    try:
        value = review_query(
            session,
            query_hash,
            status=body.status,
            reviewer=admin.subject,
            note=body.note,
        )
    except ValueError:
        raise HTTPException(422, "Invalid search suggestion hash") from None
    except LookupError:
        raise HTTPException(404, "Search suggestion not found") from None
    session.commit()
    return SearchSuggestionOut.from_model(value)
