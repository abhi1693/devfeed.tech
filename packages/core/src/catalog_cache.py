"""Immutable snapshots keyed by transactional PostgreSQL revisions, never a TTL decision."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from typing import TypeVar, cast

from sqlalchemy import select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from devfeed_core.cache import CacheUnavailable, get_cache, record_cache_read
from devfeed_core.config import get_settings
from devfeed_core.models import CatalogRevision

T = TypeVar("T")
type SnapshotKey = tuple[Engine | Connection, tuple[str, ...]]
type SnapshotRevision = tuple[tuple[str, str], ...]
type SnapshotCache = dict[SnapshotKey, tuple[SnapshotRevision, object]]

_snapshots: ContextVar[SnapshotCache | None] = ContextVar("catalog_snapshots", default=None)
_invalidator: ContextVar[Callable[[], None] | None] = ContextVar(
    "catalog_snapshot_invalidator", default=None
)


@contextmanager
def reuse_snapshots(
    *, retained: SnapshotCache | None = None, on_invalidate: Callable[[], None] | None = None
) -> Iterator[None]:
    """Reuse revision-validated snapshots within a scope or a bounded owner cache."""
    token = _snapshots.set({} if retained is None else retained)
    invalidator_token = _invalidator.set(on_invalidate)
    try:
        yield
    finally:
        _snapshots.reset(token)
        _invalidator.reset(invalidator_token)


def _forget(values: SnapshotCache | None, slot: SnapshotKey) -> None:
    if values is not None and slot in values:
        del values[slot]
        invalidate = _invalidator.get()
        if invalidate is not None:
            invalidate()


def _revisions(session: Session, names: Collection[str]) -> SnapshotRevision:
    return tuple(
        (name, str(revision))
        for name, revision in session.execute(
            select(CatalogRevision.name, CatalogRevision.revision)
            .where(CatalogRevision.name.in_(names))
            .order_by(CatalogRevision.name)
        )
    )


def snapshot_current(session: Session, names: Collection[str], value: object) -> bool:
    """Validate prepared data under the caller's locks without loading new catalog rows."""
    values = _snapshots.get()
    if values is None:
        return False
    session.flush()
    entry = values.get((session.get_bind(), tuple(sorted(names))))
    return entry is not None and entry[1] is value and _revisions(session, names) == entry[0]


def snapshot[T](session: Session, names: Collection[str], loader: Callable[[], T]) -> T:
    """A miss/failure reads PostgreSQL; a concurrent write prevents cache publication.

    UUID revisions change in the same transaction as every catalog write, including
    raw SQL. Rollback and out-of-order commits cannot reuse another snapshot's key.
    Read before and after loading so READ COMMITTED cannot cache mixed revisions.
    Existing publication locks still protect the final check/application boundary.
    """
    values = _snapshots.get()
    enabled = get_settings().cache_enabled
    if not enabled and values is None:
        record_cache_read("catalog", "bypass", "disabled")
        return loader()
    session.flush()
    before = _revisions(session, names)
    slot = (session.get_bind(), tuple(sorted(names)))
    if len(before) != len(names):
        _forget(values, slot)
        return loader()
    if values is not None and slot in values and values[slot][0] == before:
        record_cache_read("catalog", "hit")
        return cast(T, values[slot][1])
    # Retained prepared indexes can be much larger than a catalog. Release the
    # old generation before allocating its replacement, including missing keys.
    _forget(values, slot)
    if not enabled:
        record_cache_read("catalog", "bypass", "disabled")
        value = loader()
        if values is not None and _revisions(session, names) == before:
            values[slot] = (before, value)
        return value
    cache = get_cache()
    # Reuse the cache's bounded Redis failure policy, independently of public
    # response invalidation. Revisions are authoritative; expiry only bounds storage.
    # One slot per projection, not one multi-megabyte key per catalog edit.
    # A late writer may replace a newer slot, but revision comparison makes that
    # a miss, never a stale hit. Storage stays bounded during bulk ingestion.
    key = f"{cache.namespace}:catalog:v1:{','.join(sorted(names))}"
    outcome, reason = "miss", None
    try:
        body = cache._run(lambda: cache.redis.get(key))
        if body is not None:
            entry = json.loads(body)
            if entry["revision"] == [list(item) for item in before]:
                value = cast(T, entry["value"])
                record_cache_read("catalog", "hit")
                if values is not None:
                    values[slot] = (before, value)
                return value
    except CacheUnavailable:
        outcome, reason = "bypass", "cache_unavailable"
    except (ValueError, UnicodeError, KeyError, TypeError):
        reason = "invalid_entry"
    record_cache_read("catalog", outcome, reason)
    value = loader()
    if _revisions(session, names) == before:
        if values is not None:
            values[slot] = (before, value)
        with suppress(CacheUnavailable):
            cache._run(
                lambda: cache.redis.set(
                    key, json.dumps({"revision": before, "value": value}), ex=600
                )
            )
    return value
