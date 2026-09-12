"""Shared, versioned sitemap snapshots. Only the refresh owner reads PostgreSQL."""

import hashlib
import json
import logging
import time
import uuid
from contextlib import suppress
from datetime import datetime
from urllib.parse import quote

from sqlalchemy import func, literal, select, text

from devfeed_core.cache import RELEASE, CacheUnavailable, get_cache
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.models import Article, ArticleOrigin, ArticleTag, ArticleTopic, Source, Tag, Topic
from devfeed_core.publication import visible_article

KINDS = ("articles", "topics", "tags", "sources")
PART_SIZE = 1000
MAX_PARTS = 49999  # Reserve one of the index's 50,000 entries for public static pages.
SNAPSHOT_FORMAT = 4
LOCK_SECONDS = 60
BUILD_SECONDS = 45
logger = logging.getLogger(__name__)


class SitemapUnavailable(Exception):
    pass


def statements():
    yield (
        "articles",
        select(
            Article.id,
            Article.slug,
            func.coalesce(Article.published_to_feed_at, Article.discovered_at),
            Article.content_type,
        )
        .where(visible_article())
        .order_by(Article.id),
    )
    for kind, model, link, foreign, conditions in (
        (
            "topics",
            Topic,
            ArticleTopic,
            ArticleTopic.topic_id,
            [Topic.status == "active"],
        ),
        ("tags", Tag, ArticleTag, ArticleTag.tag_id, []),
        (
            "sources",
            Source,
            ArticleOrigin,
            ArticleOrigin.source_id,
            [Source.approval_status == "approved", Source.enabled.is_(True)],
        ),
    ):
        membership = (
            select(1)
            .select_from(link)
            .join(Article, Article.id == link.article_id)
            .where(foreign == model.id, visible_article())
        )
        if kind == "topics":
            membership = membership.where(ArticleTopic.role.in_(["primary", "supporting"]))
        yield (
            kind,
            select(
                model.id,
                {"topics": Topic.slug, "tags": Tag.slug, "sources": Source.slug}[kind],
                membership.with_only_columns(
                    func.max(func.coalesce(Article.published_to_feed_at, Article.discovered_at))
                ).scalar_subquery(),
                literal(None),
            )
            .where(*conditions, membership.exists())
            .order_by(model.id),
        )


def _prefix():
    return get_cache().namespace + ":sitemaps:v1"


def _redis(operation):
    # Reuse the app's Sentinel-aware pool and bounded cache circuit breaker.
    return get_cache()._run(lambda: operation(get_cache().redis))


def _read(key):
    data = _redis(lambda redis: redis.get(key))
    return json.loads(data) if data is not None else None


def _write(key, value, ttl):
    _redis(lambda redis: redis.set(key, json.dumps(value, separators=(",", ":")), ex=ttl))


def build_snapshot(prefix, token, ttl):
    generation = uuid.uuid4().hex
    parts = []
    latest_publication: dict[str, datetime] = {}
    previous = _read(prefix + ":manifest") or {}
    old_parts = {(p["kind"], p["page"]): p for p in previous.get("parts", [])}

    def store_part(kind, page, entries):
        # Stable filenames only change their lastmod when their XML content changes.
        digest = hashlib.sha256(json.dumps(entries).encode()).hexdigest()
        old = old_parts.get((kind, page), {})
        modified_at = old.get("modified_at") if old.get("digest") == digest else None
        _write(
            f"{prefix}:{generation}:{kind}:{page}",
            {"paths": [entry["path"] for entry in entries], "entries": entries},
            ttl + LOCK_SECONDS,
        )
        parts.append(
            {
                "kind": kind,
                "page": page,
                "digest": digest,
                "modified_at": modified_at or time.time(),
            }
        )
        if len(parts) > MAX_PARTS:
            raise SitemapUnavailable("Sitemap index limit reached")

    deadline = time.monotonic() + BUILD_SECONDS
    # A read-only snapshot makes every shard agree about eligibility and membership.
    with session_factory()() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout = '40s'"))
        for kind, statement in statements():
            page, entries = 1, []
            for _, slug, published_at, content_type in session.execute(
                statement.execution_options(yield_per=PART_SIZE)
            ):
                if time.monotonic() > deadline:
                    raise SitemapUnavailable("Sitemap refresh exceeded its budget")
                entry = {"path": f"/{kind}/" + quote(str(slug), safe="")}
                if published_at is not None:
                    entry["lastmod"] = published_at.isoformat()
                    for key in [kind, *([f"feed:{content_type}"] if content_type else [])]:
                        latest_publication[key] = max(
                            latest_publication.get(key, published_at), published_at
                        )
                entries.append(entry)
                if len(entries) == PART_SIZE:
                    store_part(kind, page, entries)
                    page, entries = page + 1, []
            if entries:
                store_part(kind, page, entries)
    manifest = {
        "format": SNAPSHOT_FORMAT,
        "generation": generation,
        "built_at": time.time(),
        "parts": parts,
        "latest_publication": {
            kind: value.isoformat() for kind, value in latest_publication.items()
        },
    }
    # Publish only after all shards exist, and only while this refresh owns its lease.
    published = _redis(
        lambda redis: redis.eval(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3]); return 1 end; return 0",
            2,
            prefix + ":lock",
            prefix + ":manifest",
            token,
            json.dumps(manifest, separators=(",", ":")),
            ttl,
        )
    )
    if not published:
        raise SitemapUnavailable("Sitemap refresh lease expired")
    return manifest


def manifest():
    prefix = _prefix()
    previous = None
    token = uuid.uuid4().hex
    owns = False
    try:
        previous = _read(prefix + ":manifest")
        if previous and previous.get("format") != SNAPSHOT_FORMAT:
            previous = None
        refresh = get_settings().sitemap_refresh_seconds
        if previous and time.time() - previous["built_at"] < refresh:
            return previous
        if _redis(lambda redis: redis.exists(prefix + ":retry")):
            if previous:
                return previous
            raise SitemapUnavailable("Sitemap refresh is cooling down")
        owns = _redis(lambda redis: redis.set(prefix + ":lock", token, nx=True, ex=LOCK_SECONDS))
        if not owns:
            if previous:
                return previous
            raise SitemapUnavailable("Sitemap is being prepared")
        # Another owner can finish between our first read and lease acquisition.
        current = _read(prefix + ":manifest")
        if current and current.get("format") == SNAPSHOT_FORMAT:
            previous = current
            if time.time() - current["built_at"] < refresh:
                return current
        return build_snapshot(prefix, token, refresh * 4 + 300)
    except Exception as exc:
        if owns:
            logger.warning("sitemap_refresh_failed", extra={"error_type": type(exc).__name__})
            with suppress(CacheUnavailable):
                _redis(lambda redis: redis.set(prefix + ":retry", "1", ex=60))
        if previous:
            return previous
        raise SitemapUnavailable("Sitemap is temporarily unavailable") from exc
    finally:
        if owns:
            with suppress(CacheUnavailable):
                _redis(lambda redis: redis.eval(RELEASE, 1, prefix + ":lock", token))


def part(kind, page, generation=None):
    if generation is None:
        generation = manifest()["generation"]
    try:
        return _read(f"{_prefix()}:{generation}:{kind}:{page}")
    except CacheUnavailable as exc:
        raise SitemapUnavailable("Sitemap is temporarily unavailable") from exc
