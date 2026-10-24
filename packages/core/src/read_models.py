"""Shared durable read-model contracts; projection work never runs in board GETs."""

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from devfeed_core.config import get_settings
from devfeed_core.models import ReadModelEvent, ReadModelState, utcnow

PIPELINE_POLICY_VERSION = "pipeline-v2"
EventKind = Literal[
    "article",
    "source",
    "candidate",
    "catalog",
    "policy",
    "backfill_article",
    "backfill_source",
    "backfill_candidate",
    "source_fanout",
]


class ReadModelSnapshot(BaseModel):
    status: Literal["fresh", "stale", "refreshing", "catching_up", "rebuilding"]
    generated_at: datetime | None = None
    generation: str | None = None
    policy_version: str | None = None
    article_generation: int | None = Field(default=None, ge=0)
    catalog_generation: int | None = Field(default=None, ge=0)
    catalog_revision: list[list[str]] | None = None
    catalog_target_revision: list[list[str]] | None = None
    catalog_lag_seconds: float | None = Field(default=None, ge=0)
    lag_seconds: float | None = Field(default=None, ge=0)


def pipeline_policy_version(settings=None) -> str:
    """Configuration and code changes invalidate every affected derived decision."""
    from devfeed_core.analysis import PROMPT_VERSION
    from devfeed_core.article_analysis_steps import STEP_SPECS
    from devfeed_core.logos import LOGO_VERSION
    from devfeed_core.managed_images import IMAGE_VERSION, THUMBNAIL_VERSION
    from devfeed_core.source_relevance import VERSION

    settings = settings or get_settings()
    payload = {
        "version": PIPELINE_POLICY_VERSION,
        "prompt": PROMPT_VERSION,
        "steps": {name: spec.prompt_version for name, spec in STEP_SPECS.items()},
        "images": (IMAGE_VERSION, THUMBNAIL_VERSION, LOGO_VERSION),
        "source_relevance": VERSION,
        "settings": {
            name: getattr(settings, name)
            for name in (
                "ai_enabled",
                "full_automation",
                "image_storage_enabled",
                "image_public_url",
                "analysis_max_candidates",
                "analysis_fallback_candidates",
                "ai_content_not_before",
            )
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def enqueue_event(session, kind: EventKind, entity_id=None, *, after_id=None):
    """Enqueue in the caller's transaction, including tombstones without foreign keys."""
    event = ReadModelEvent(kind=kind, entity_id=entity_id, after_id=after_id)
    session.add(event)
    return event


def event_batch(session, limit: int = 200) -> list[ReadModelEvent]:
    """Caller holds the projector singleton; commits are not ordered by event ID."""
    if limit < 1:
        raise ValueError("Read-model event batch size must be positive")
    return list(session.scalars(select(ReadModelEvent).order_by(ReadModelEvent.id).limit(limit)))


def acknowledge_events(session, event_ids: Iterable[int]) -> int:
    """Acknowledge captured IDs only, preserving older IDs committed concurrently."""
    identifiers = list(event_ids)
    if not identifiers:
        return 0
    result = session.execute(delete(ReadModelEvent).where(ReadModelEvent.id.in_(identifiers)))
    return result.rowcount


def get_state(session, name: str) -> ReadModelState | None:
    return session.get(ReadModelState, name)


def set_state(session, name: str, payload: dict) -> None:
    statement = insert(ReadModelState).values(name=name, payload=payload, updated_at=utcnow())
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[ReadModelState.name],
            set_={
                "payload": statement.excluded.payload,
                "updated_at": statement.excluded.updated_at,
            },
        )
    )
    # Core upserts do not synchronize an already loaded ORM identity. A retained
    # state object must observe the new generation within this transaction too.
    stored = session.identity_map.get(session.identity_key(ReadModelState, (name,)))
    if stored is not None:
        session.expire(stored, ["payload", "updated_at"])
