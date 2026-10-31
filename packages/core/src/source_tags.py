"""Import explicit feed labels independently of inferred article classifications."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
import zlib
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from types import MappingProxyType

from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, SessionTransaction, lazyload

from devfeed_core.cache import CacheUnavailable, ResponseCache, get_cache, record_cache_read
from devfeed_core.config import get_settings
from devfeed_core.feeds.parser import plain_text
from devfeed_core.models import Article, ArticleTag, CatalogRevision, Tag
from devfeed_core.tag_names import normalize_tag_name


def source_tag_names(values: Iterable[object]) -> dict[str, str]:
    names: dict[str, str] = {}
    for value in values:
        if not isinstance(value, str):
            continue
        name = normalize_tag_name(plain_text(unicodedata.normalize("NFKC", value), 1000))[:100]
        if name and any(character.isalnum() for character in name):
            names.setdefault(name.casefold(), name)
    return names


def source_tag_slug(name: str) -> str:
    name = normalize_tag_name(name)
    normalized = unicodedata.normalize("NFKD", name.casefold())
    normalized = normalized.replace("+", " plus ").replace("#", " sharp ")
    ascii_name = normalized.encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    digest = hashlib.sha256(name.casefold().encode()).hexdigest()[:12]
    if not slug:
        return f"tag-{digest}"
    if any(character.isalnum() and not character.isascii() for character in normalized):
        return f"{slug[:87].rstrip('-')}-{digest}"
    return slug if len(slug) <= 100 else f"{slug[:87].rstrip('-')}-{digest}"


# A single immutable projection, keyed by the transactional catalog revision.
# Do not retain ORM objects, sessions, or prior catalog generations.
type SourceTagIndex = tuple[Mapping[str, uuid.UUID], Mapping[str, uuid.UUID]]
_identity_snapshot: tuple[tuple[Engine | Connection, uuid.UUID | None], *SourceTagIndex] | None = (
    None
)


# A separate projection slot cannot collide with the generic tags catalog cache.
# One latest generation bounds storage across edits; revisions decide validity.
_SHARED_INDEX_SLOT = "source-tag-identities:v1"
_SHARED_INDEX_BYTES = 4_000_000
_SHARED_INDEX_DECODED_BYTES = 16_000_000
_SHARED_INDEX_TTL = 600
_SHARED_INDEX_MAX_TAGS = 100_000
_PENDING_INDEX = "devfeed_source_tag_projection_pending"
_prefetched_projection: ContextVar[tuple[str, uuid.UUID, SourceTagIndex] | None] = ContextVar(
    "source_tag_projection", default=None
)


def _decode_identity_map(value: object, identifiers: list[uuid.UUID]) -> Mapping[str, uuid.UUID]:
    if not isinstance(value, dict):
        raise ValueError("Invalid source tag identity projection")
    result = {}
    for key, position in value.items():
        if not isinstance(key, str) or not key or len(key) > 300:
            raise ValueError("Invalid source tag identity")
        if type(position) is not int or not 0 <= position < len(identifiers):
            raise ValueError("Invalid source tag identifier reference")
        result[key] = identifiers[position]
    return MappingProxyType(result)


def _decode_shared_source_tags(
    body: bytes, revision: uuid.UUID | None
) -> tuple[uuid.UUID, SourceTagIndex] | None:
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(body, _SHARED_INDEX_DECODED_BYTES + 1)
    if (
        len(decoded) > _SHARED_INDEX_DECODED_BYTES
        or not decoder.eof
        or decoder.unconsumed_tail
        or decoder.unused_data
    ):
        raise ValueError("Invalid or oversized source tag identity projection")
    value = json.loads(decoded)
    stored_revision = uuid.UUID(value["revision"])
    if revision is not None and stored_revision != revision:
        return None
    raw_ids = value["ids"]
    if (
        not isinstance(raw_ids, list)
        or len(raw_ids) > _SHARED_INDEX_MAX_TAGS
        or any(not isinstance(item, str) for item in raw_ids)
    ):
        raise ValueError("Invalid source tag identifier table")
    identifiers = [uuid.UUID(item) for item in raw_ids]
    return stored_revision, (
        _decode_identity_map(value["identities"], identifiers),
        _decode_identity_map(value["slugs"], identifiers),
    )


def _shared_source_tag_index(
    revision: uuid.UUID | None,
) -> tuple[uuid.UUID, SourceTagIndex] | None:
    cache = get_cache()
    try:
        body = cache._run(
            lambda: cache.redis.getrange(
                f"{cache.namespace}:{_SHARED_INDEX_SLOT}", 0, _SHARED_INDEX_BYTES
            )
        )
        if not body or len(body) > _SHARED_INDEX_BYTES:
            record_cache_read("catalog", "miss")
            return None
        if not isinstance(body, bytes):
            raise ValueError("Invalid source tag projection transport")
        result = _decode_shared_source_tags(body, revision)
        if result is None:
            record_cache_read("catalog", "miss", "stale")
        return result
    except CacheUnavailable:
        record_cache_read("catalog", "bypass", "cache_unavailable")
    except (
        ValueError,
        UnicodeError,
        KeyError,
        TypeError,
        AttributeError,
        RecursionError,
        zlib.error,
    ):
        record_cache_read("catalog", "miss", "invalid_entry")
    return None


@contextmanager
def prefetch_source_tags(values: Iterable[object]) -> Iterator[None]:
    """Read/decode optional Redis data before the caller opens its SQL transaction."""
    settings = get_settings()
    candidate = None
    if (
        settings.cache_enabled
        and settings.source_tag_shared_cache_enabled
        and _identity_snapshot is None
        and any(values)
    ):
        found = _shared_source_tag_index(None)
        if found is not None:
            candidate = (get_cache().namespace, *found)
    token = _prefetched_projection.set(candidate)
    try:
        yield
    finally:
        _prefetched_projection.reset(token)


def _publish_source_tag_index(
    revision: uuid.UUID, index: SourceTagIndex, cache: ResponseCache | None = None
) -> None:
    cache = cache or get_cache()
    try:
        cache._run(lambda: None)  # Do not serialize a large payload behind an open breaker.
    except CacheUnavailable:
        return
    identifiers = sorted(set(index[0].values()) | set(index[1].values()))
    if len(identifiers) > _SHARED_INDEX_MAX_TAGS:
        return
    positions = {identifier: position for position, identifier in enumerate(identifiers)}
    value = json.dumps(
        {
            "revision": str(revision),
            "ids": [str(identifier) for identifier in identifiers],
            "identities": {key: positions[identifier] for key, identifier in index[0].items()},
            "slugs": {key: positions[identifier] for key, identifier in index[1].items()},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    if len(value) > _SHARED_INDEX_DECODED_BYTES:
        return
    value = zlib.compress(value, level=1)
    if len(value) > _SHARED_INDEX_BYTES:
        return
    with suppress(CacheUnavailable):
        cache._run(
            lambda: cache.redis.set(
                f"{cache.namespace}:{_SHARED_INDEX_SLOT}", value, ex=_SHARED_INDEX_TTL
            )
        )


@event.listens_for(Session, "after_transaction_end")
def _publish_pending_source_tags(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is not None:
        return
    pending = session.info.pop(_PENDING_INDEX, None)
    if pending is not None:
        # SQLAlchemy has closed the root transaction and returned its connection
        # before this event. Compression and Redis cannot extend SQL lock life.
        revision, index, cache = pending
        _publish_source_tag_index(revision, index, cache)


@event.listens_for(Session, "after_rollback")
def _cancel_pending_source_tags(session: Session) -> None:
    session.info.pop(_PENDING_INDEX, None)


def _build_source_tag_index(session: Session) -> SourceTagIndex:
    tags = session.execute(select(Tag.id, Tag.name, Tag.slug, Tag.aliases).order_by(Tag.slug)).all()
    identities: dict[str, uuid.UUID] = {}
    # Exact names/slugs take priority over aliases, with a deterministic tie-break.
    for tag in tags:
        for name in source_tag_names([tag.name, tag.slug]):
            identities.setdefault(name, tag.id)
    for tag in tags:
        for name in source_tag_names(tag.aliases):
            identities.setdefault(name, tag.id)
    result = (MappingProxyType(identities), MappingProxyType({tag.slug: tag.id for tag in tags}))
    return result


def _source_tag_index(session: Session) -> SourceTagIndex:
    global _identity_snapshot
    session.flush()
    revision_query = select(CatalogRevision.revision).where(CatalogRevision.name == "tags")
    revision = session.scalar(revision_query)
    key = (session.get_bind(), revision)
    saved = _identity_snapshot
    if revision is not None and saved is not None and saved[0] == key:
        return saved[1:]
    settings = get_settings()
    shared = (
        settings.cache_enabled and settings.source_tag_shared_cache_enabled and revision is not None
    )
    if shared and revision is not None:
        prefetched = _prefetched_projection.get()
        cached = (
            prefetched[2]
            if prefetched is not None and prefetched[:2] == (get_cache().namespace, revision)
            else None
        )
        if prefetched is not None and cached is None:
            record_cache_read("catalog", "miss", "stale")
        if cached is not None and session.scalar(revision_query) == revision:
            record_cache_read("catalog", "hit")
            _identity_snapshot = (key, *cached)
            return cached
        if cached is not None:
            record_cache_read("catalog", "miss", "stale")
            # The failed fence already proves this candidate cannot be cached.
            # Read current rows without a third revision read or publication.
            return _build_source_tag_index(session)
    result = _build_source_tag_index(session)
    if revision is not None and session.scalar(revision_query) == revision:
        _identity_snapshot = (key, *result)
        if shared:
            session.info[_PENDING_INDEX] = (revision, result, get_cache())
    return result


def resolve_source_tags(
    session: Session, values: Iterable[object]
) -> tuple[dict[str, uuid.UUID], int]:
    names = source_tag_names(values)
    if not names:
        return {}, 0
    identities, cached_slugs = _source_tag_index(session)
    by_slug = cached_slugs
    resolved: dict[str, uuid.UUID] = {}
    missing: dict[str, str] = {}
    for key, name in sorted(names.items()):
        slug = source_tag_slug(name)
        identifier = identities.get(key) or by_slug.get(slug)
        if identifier:
            resolved[key] = identifier
        else:
            missing.setdefault(slug, name)
    created = 0
    if missing:
        by_slug = dict(cached_slugs)
        # Resolve all labels in slug order before taking article locks, including
        # overlapping imports by different workers. Never overwrite admin edits.
        created = len(
            session.scalars(
                insert(Tag)
                .values(
                    [
                        {"name": missing[slug], "slug": slug, "aliases": []}
                        for slug in sorted(missing)
                    ]
                )
                .on_conflict_do_nothing(index_elements=[Tag.slug])
                .returning(Tag.id)
            ).all()
        )
        by_slug.update(
            session.execute(select(Tag.slug, Tag.id).where(Tag.slug.in_(missing))).tuples().all()
        )
    for key, name in names.items():
        if key not in resolved:
            resolved[key] = by_slug[source_tag_slug(name)]
    return resolved, created


def attach_source_tags(
    session: Session, article_id: uuid.UUID, tag_ids: Iterable[uuid.UUID]
) -> int:
    identifiers = sorted(set(tag_ids))
    if not identifiers:
        return 0
    article = session.scalar(
        select(Article)
        .options(lazyload("*"))
        .where(Article.id == article_id)
        .with_for_update(of=Article)
    )
    if article is None or (article.classification_provenance or {}).get("origin") == "manual":
        return 0  # An explicit classification can remove a supplied tag permanently.
    saved = session.scalars(
        insert(ArticleTag)
        .values(
            [
                {"article_id": article_id, "tag_id": tag_id, "origin": "source"}
                for tag_id in identifiers
            ]
        )
        .on_conflict_do_update(
            index_elements=[ArticleTag.article_id, ArticleTag.tag_id],
            set_={"origin": "source"},
            where=ArticleTag.origin.in_(["heuristic", "ai"]),
        )
        .returning(ArticleTag.tag_id)
    ).all()
    session.expire(article, ["tags"])
    return len(saved)
