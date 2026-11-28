"""Private Typesense search analytics for the administration dashboard."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from devfeed_admin_api.auth import Admin

router = APIRouter(prefix="/v1/admin", tags=["admin-search-analytics"])


class SearchAnalyticsQuery(BaseModel):
    query: str
    count: int = Field(ge=0)


class SearchAnalytics(BaseModel):
    generated_at: datetime
    enabled: bool
    flush_interval_seconds: int
    rules: list[str]
    popular_queries: list[SearchAnalyticsQuery]
    nohits_queries: list[SearchAnalyticsQuery]


@router.get("/search-analytics", response_model=SearchAnalytics)
def search_analytics(admin: Admin, limit: int = Query(default=25, ge=1, le=100)) -> SearchAnalytics:
    raise HTTPException(410, "This analytics report has been retired.")
