"""Moderated search suggestions ranked by direct ClickHouse observations."""

import hashlib
import heapq
import unicodedata
import uuid
from datetime import UTC, datetime
from itertools import chain
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from starlette.concurrency import run_in_threadpool

from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.analytics_direct_engagement import DirectEngagementAnalytics
from devfeed_core.models import SearchQueryStat
from devfeed_core.schemas import validate_database_text

MAX_QUERY_LENGTH = 200
MAX_QUERY_WORDS = 20
CANDIDATE_PAGE_SIZE = 500
SEARCH_IMPORT_ID = "direct-v1"


def normalize_query(value: str, *, casefold: bool = True, allow_empty: bool = False) -> str:
    validate_database_text(value)
    query = " ".join(unicodedata.normalize("NFKC", value).split())
    if casefold:
        query = query.casefold()
    if len(query) > MAX_QUERY_LENGTH or len(query.split()) > MAX_QUERY_WORDS:
        raise ValueError("Search query is too long")
    if not allow_empty and (not query or not any(character.isalnum() for character in query)):
        raise ValueError("Search query is empty")
    return query


def query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def record_successful_query(session, value: str) -> str:
    """Ensure review metadata exists; analytical counts live in ClickHouse."""
    query = normalize_query(value)
    digest = query_hash(query)
    statement = insert(SearchQueryStat).values(query_hash=digest, query=query)
    session.execute(statement.on_conflict_do_nothing(index_elements=[SearchQueryStat.query_hash]))
    return digest


def _metadata_page(factory, *, cursor, prefix, status):
    statement = (
        select(
            SearchQueryStat.query_hash,
            SearchQueryStat.query,
            SearchQueryStat.status,
            SearchQueryStat.reviewed_at,
            SearchQueryStat.review_note,
        )
        .order_by(SearchQueryStat.query_hash)
        .limit(CANDIDATE_PAGE_SIZE)
    )
    if cursor is not None:
        statement = statement.where(SearchQueryStat.query_hash > cursor)
    if prefix is not None:
        statement = statement.where(SearchQueryStat.query.startswith(prefix, autoescape=True))
    if status is not None:
        statement = statement.where(SearchQueryStat.status == status)
    with factory() as session:
        return [dict(row) for row in session.execute(statement).mappings().all()]


class SearchSuggestionAnalytics:
    """Rank PG moderation metadata using only ClickHouse analytical counts."""

    def __init__(self, client):
        self.client = client
        self.store = DirectEngagementAnalytics(client)

    async def require_history(self):
        rows = await self.client.query(
            "SELECT complete FROM analytics_backfill_progress FINAL "
            "WHERE import_id={import_id:String} AND source='search_query_stats'",
            {"import_id": SEARCH_IMPORT_ID},
        )
        if len(rows) != 1 or rows[0].get("complete") != 1:
            raise AnalyticsUnavailable("Search analytics history is still importing")

    async def record(self, digest, *, event_id=None, now=None):
        await self.store.search_events(
            [
                {
                    "query_hash": digest,
                    "event_id": event_id or uuid.uuid4(),
                    "occurred_at": now or datetime.now(UTC),
                }
            ]
        )

    async def rank(self, factory, *, prefix=None, status=None, minimum=0, limit=100):
        if prefix is not None:
            prefix = normalize_query(prefix) if prefix.strip() else ""
            if not prefix:
                return []
        await self.require_history()
        limit = min(max(limit, 1), 500)
        minimum = max(0, minimum)
        cursor = None
        best: list[dict[str, Any]] = []
        while True:
            page = await run_in_threadpool(
                _metadata_page, factory, cursor=cursor, prefix=prefix, status=status
            )
            if not page:
                return best
            stats = await self.store.search_counts([row["query_hash"] for row in page])
            counts = {row["query_hash"]: row for row in stats}
            candidates = []
            for item in page:
                metrics = counts.get(item["query_hash"])
                if metrics is None:
                    continue
                count = metrics.get("successful_count")
                if type(count) is not int or count < 0:
                    raise AnalyticsUnavailable("Invalid search analytics result")
                if count >= minimum:
                    candidates.append(
                        item
                        | {
                            "successful_count": count,
                            "first_seen_at": metrics["first_seen"],
                            "last_seen_at": metrics["last_seen"],
                        }
                    )
            best = heapq.nsmallest(
                limit,
                chain(best, candidates),
                key=lambda item: (-item["successful_count"], item["query"]),
            )
            cursor = page[-1]["query_hash"]
            if len(page) < CANDIDATE_PAGE_SIZE:
                return best

    async def get(self, digest):
        await self.require_history()
        rows = await self.store.search_counts([digest])
        if len(rows) != 1:
            raise AnalyticsUnavailable("Search analytics are unavailable")
        row = rows[0]
        count = row.get("successful_count")
        if type(count) is not int or count < 0:
            raise AnalyticsUnavailable("Invalid search analytics result")
        return {
            "successful_count": count,
            "first_seen_at": row["first_seen"],
            "last_seen_at": row["last_seen"],
        }


def review_query(session, digest: str, *, status: str, reviewer: str, note: str | None):
    if not (len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)):
        raise ValueError("Invalid query hash")
    query = session.get(SearchQueryStat, digest)
    if query is None:
        raise LookupError("Search query not found")
    query.status = status
    query.reviewed_at = datetime.now(UTC)
    query.reviewed_by = reviewer
    query.review_note = note
    return query
