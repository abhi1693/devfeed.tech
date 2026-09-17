"""Immutable snapshots keyed by transactional PostgreSQL revisions, never a TTL decision."""

import json
from contextlib import suppress

from sqlalchemy import select

from devfeed_core.cache import CacheUnavailable, get_cache
from devfeed_core.config import get_settings
from devfeed_core.models import CatalogRevision


def snapshot(session, names, loader):
    """A miss/failure reads PostgreSQL; a concurrent write prevents cache publication.

    UUID revisions change in the same transaction as every catalog write, including
    raw SQL. Rollback and out-of-order commits cannot reuse another snapshot's key.
    Read before and after loading so READ COMMITTED cannot cache mixed revisions.
    Existing publication locks still protect the final check/application boundary.
    """
    if not get_settings().cache_enabled:
        return loader()
    session.flush()

    def revisions():
        return tuple(
            (name, str(revision))
            for name, revision in session.execute(
                select(CatalogRevision.name, CatalogRevision.revision)
                .where(CatalogRevision.name.in_(names))
                .order_by(CatalogRevision.name)
            )
        )

    before = revisions()
    if len(before) != len(names):
        return loader()
    cache = get_cache()
    # Reuse the cache's bounded Redis failure policy, independently of public
    # response invalidation. Revisions are authoritative; expiry only bounds storage.
    key = f"{cache.namespace}:catalog:v1:{json.dumps(before)}"
    with suppress(CacheUnavailable, ValueError, UnicodeError):
        body = cache._run(lambda: cache.redis.get(key))
        if body is not None:
            return json.loads(body)
    value = loader()
    if revisions() == before:
        with suppress(CacheUnavailable):
            cache._run(lambda: cache.redis.set(key, json.dumps(value), ex=600))
    return value
