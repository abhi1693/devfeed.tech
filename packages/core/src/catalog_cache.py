"""Immutable snapshots keyed by transactional PostgreSQL revisions, never a TTL decision."""

import json
from contextlib import contextmanager, suppress
from contextvars import ContextVar

from sqlalchemy import select

from devfeed_core.cache import CacheUnavailable, get_cache, record_cache_read
from devfeed_core.config import get_settings
from devfeed_core.models import CatalogRevision

_snapshots: ContextVar[dict | None] = ContextVar("catalog_snapshots", default=None)


@contextmanager
def reuse_snapshots():
    """Reuse one snapshot per projection during a tick, always checking PostgreSQL."""
    token = _snapshots.set({})
    try:
        yield
    finally:
        _snapshots.reset(token)


def _revisions(session, names):
    return tuple(
        (name, str(revision))
        for name, revision in session.execute(
            select(CatalogRevision.name, CatalogRevision.revision)
            .where(CatalogRevision.name.in_(names))
            .order_by(CatalogRevision.name)
        )
    )


def snapshot_current(session, names, value) -> bool:
    """Validate prepared data under the caller's locks without loading new catalog rows."""
    values = _snapshots.get()
    if values is None:
        return False
    session.flush()
    entry = values.get((session.get_bind(), tuple(sorted(names))))
    return entry is not None and entry[1] is value and _revisions(session, names) == entry[0]


def snapshot(session, names, loader):
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
    if len(before) != len(names):
        return loader()
    slot = (session.get_bind(), tuple(sorted(names)))
    if values is not None and slot in values and values[slot][0] == before:
        record_cache_read("catalog", "hit")
        return values[slot][1]
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
                record_cache_read("catalog", "hit")
                if values is not None:
                    values[slot] = (before, entry["value"])
                return entry["value"]
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
