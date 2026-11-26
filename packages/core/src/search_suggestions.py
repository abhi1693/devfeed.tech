"""PostgreSQL moderation and normalization for editorial search suggestions."""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from devfeed_core.models import SearchQueryStat
from devfeed_core.schemas import validate_database_text

MAX_QUERY_LENGTH = 200
MAX_QUERY_WORDS = 20


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


def record_successful_query(session: Session, value: str) -> str:
    """Ensure review metadata exists for an editorial suggestion."""
    query = normalize_query(value)
    digest = query_hash(query)
    statement = insert(SearchQueryStat).values(query_hash=digest, query=query)
    session.execute(statement.on_conflict_do_nothing(index_elements=[SearchQueryStat.query_hash]))
    return digest


def review_query(
    session: Session, digest: str, *, status: str, reviewer: str, note: str | None
) -> SearchQueryStat:
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
