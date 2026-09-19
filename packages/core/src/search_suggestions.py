"""Identity-free successful-query aggregates and moderated suggestions."""

import hashlib
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from devfeed_core.models import SearchQueryStat

MAX_QUERY_LENGTH = 200
MAX_QUERY_WORDS = 20


def normalize_query(value: str) -> str:
    query = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if len(query) > MAX_QUERY_LENGTH or len(query.split()) > MAX_QUERY_WORDS:
        raise ValueError("Search query is too long")
    if not query or not any(character.isalnum() for character in query):
        raise ValueError("Search query is empty")
    return query


def query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def record_successful_query(session, value: str) -> str:
    """Increment only the aggregate; no account, IP, session, or result is stored."""
    query = normalize_query(value)
    digest = query_hash(query)
    now = datetime.now(UTC)
    statement = insert(SearchQueryStat).values(
        query_hash=digest,
        query=query,
        successful_count=1,
        first_seen_at=now,
        last_seen_at=now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[SearchQueryStat.query_hash],
        set_={
            "successful_count": SearchQueryStat.successful_count + 1,
            "last_seen_at": now,
        },
    )
    session.execute(statement)
    return digest


def approved_suggestions(session, prefix: str, *, limit: int, minimum: int) -> list[str]:
    prefix = normalize_query(prefix) if prefix.strip() else ""
    if not prefix:
        return []
    statement = (
        select(SearchQueryStat.query)
        .where(
            SearchQueryStat.status == "approved",
            SearchQueryStat.successful_count >= minimum,
            SearchQueryStat.query.startswith(prefix, autoescape=True),
        )
        .order_by(SearchQueryStat.successful_count.desc(), SearchQueryStat.query)
        .limit(limit)
    )
    return list(session.scalars(statement))


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
