"""One federated index request plus bounded, authoritative public-record lookups."""

import time
import unicodedata
import uuid
from typing import Literal

from devfeed_core.search_engine import KINDS, MAX_PAGE, PAGE_SIZE, SearchUnavailable, Typesense
from devfeed_core.search_records import hit, public_records
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from devfeed_api.cache import CachedReadRoute
from devfeed_api.dependencies import DB

router = APIRouter(prefix="/v1/search", tags=["discovery"], route_class=CachedReadRoute)


class SearchHit(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    href: str
    image_url: str | None
    label: str
    published_at: str | None


class SearchSection(BaseModel):
    items: list[SearchHit]
    next_cursor: str | None


class SearchResponse(BaseModel):
    query: str
    sections: dict[Literal["articles", "topics", "sources", "tags"], SearchSection]


@router.get("", response_model=SearchResponse)
def search(
    session: DB,
    q: str = Query("", max_length=200),
    section: str | None = Query(None, pattern="^(articles|topics|sources|tags)$"),
    page: int = Query(1, ge=1, le=MAX_PAGE),
):
    query = " ".join(unicodedata.normalize("NFKC", q).split())
    kinds = (section,) if section else KINDS
    if not any(character.isalnum() for character in query):
        return {
            "query": query,
            "sections": {kind: {"items": [], "next_cursor": None} for kind in kinds},
        }
    if len(query.split()) > 20:
        raise HTTPException(422, "Search supports up to 20 words")
    deadline = time.monotonic() + 3.0
    try:
        matches = Typesense().search(query, kinds, page)
        sections = {}
        for kind in kinds:
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise SearchUnavailable("Search timed out")
            session.execute(select_timeout(), {"timeout": str(remaining)})
            ids = list(
                dict.fromkeys(uuid.UUID(item["document"]["id"]) for item in matches[kind]["hits"])
            )
            records = public_records(session, kind, ids)
            sections[kind] = {
                "items": [hit(kind, records[value]) for value in ids if value in records],
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
