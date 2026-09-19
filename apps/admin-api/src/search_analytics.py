"""Private Typesense search analytics for the administration dashboard."""

from datetime import datetime

from devfeed_core.models import utcnow
from devfeed_core.search_engine import (
    ANALYTICS_NOHITS_COLLECTION,
    ANALYTICS_QUERY_COLLECTION,
    SearchUnavailable,
    Typesense,
)
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
def search_analytics(admin: Admin, limit: int = Query(default=25, ge=1, le=100)):
    """Return aggregated Typesense queries for the authenticated admin UI."""
    from devfeed_core.config import get_settings

    settings = get_settings()
    now = utcnow()
    if not settings.search_analytics_enabled:
        return SearchAnalytics(
            generated_at=now,
            enabled=False,
            flush_interval_seconds=0,
            rules=[],
            popular_queries=[],
            nohits_queries=[],
        )
    try:
        engine = Typesense(admin=True)
        rules = engine.analytics_rules()
        popular = engine.analytics_queries(
            f"{engine.prefix}_{ANALYTICS_QUERY_COLLECTION}", limit=limit
        )
        nohits = engine.analytics_queries(
            f"{engine.prefix}_{ANALYTICS_NOHITS_COLLECTION}", limit=limit
        )
    except SearchUnavailable as exc:
        raise HTTPException(503, "Search analytics is temporarily unavailable") from exc
    return SearchAnalytics(
        generated_at=now,
        enabled=True,
        flush_interval_seconds=60,
        rules=sorted(
            rule.get("name")
            for rule in rules
            if isinstance(rule, dict) and isinstance(rule.get("name"), str)
        ),
        popular_queries=[SearchAnalyticsQuery.model_validate(item) for item in popular],
        nohits_queries=[SearchAnalyticsQuery.model_validate(item) for item in nohits],
    )
