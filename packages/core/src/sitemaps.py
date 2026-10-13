"""Shared, versioned sitemap snapshots. Only the refresh owner reads PostgreSQL."""

import hashlib
import json
import logging
import time
import uuid
from contextlib import suppress
from datetime import datetime
from itertools import groupby
from urllib.parse import quote

from sqlalchemy import func, literal, select, text, union_all

from devfeed_core.cache import RELEASE, CacheUnavailable, get_sitemap_cache
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.models import Article, ArticleOrigin, ArticleTag, ArticleTopic, Source, Tag, Topic
from devfeed_core.publication import visible_article

KINDS = ("articles", "topics", "tags", "sources")
PART_SIZE = 1000
MAX_PARTS = 49999  # Reserve one of the index's 50,000 entries for public static pages.
SNAPSHOT_FORMAT = 5
LOCK_SECONDS = 60
BUILD_SECONDS = 45
PREVIOUS_SECONDS = 300
logger = logging.getLogger(__name__)


class SitemapUnavailable(Exception):
    pass


def snapshot_statement():
    # One eligibility evaluation shared by articles and every discovery page.
    visible = (
        select(
            Article.id.label("article_id"),
            Article.slug,
            func.coalesce(Article.published_to_feed_at, Article.discovered_at).label("lastmod"),
            Article.content_type,
            Article.language,
        )
        .where(visible_article())
        .cte("visible_articles")
        .prefix_with("MATERIALIZED")
    )
    branches = [
        select(
            literal("articles").label("kind"),
            visible.c.article_id.label("id"),
            visible.c.slug,
            visible.c.lastmod,
            visible.c.content_type,
        )
    ]
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
            select(foreign.label("identity_id"), func.max(visible.c.lastmod).label("lastmod"))
            .select_from(link)
            .join(visible, visible.c.article_id == link.article_id)
        )
        # Anonymous collection feeds default to English, unlike article detail pages.
        if kind in {"topics", "tags"}:
            membership = membership.where(visible.c.language == "en")
        if kind == "topics":
            membership = membership.where(ArticleTopic.role.in_(["primary", "supporting"]))
        members = membership.group_by(foreign).subquery()
        branches.append(
            select(
                literal(kind).label("kind"),
                model.id,
                {"topics": Topic.slug, "tags": Tag.slug, "sources": Source.slug}[kind],
                members.c.lastmod,
                literal(None),
            )
            .join(members, members.c.identity_id == model.id)
            .where(*conditions)
        )
    return union_all(*branches).order_by("kind", "id")


def _prefix():
    return get_sitemap_cache().namespace + ":sitemaps:v1"


def _redis(operation):
    cache = get_sitemap_cache()
    return cache._run(lambda: operation(cache.redis))


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
            LOCK_SECONDS + PREVIOUS_SECONDS,
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
        rows = session.execute(snapshot_statement().execution_options(yield_per=PART_SIZE))
        for kind, members in groupby(rows, key=lambda row: row.kind):
            page, entries = 1, []
            for _, _, slug, published_at, content_type in members:
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
    new_keys = [f"{prefix}:{generation}:{p['kind']}:{p['page']}" for p in parts]
    old_keys = [
        f"{prefix}:{previous['generation']}:{p['kind']}:{p['page']}"
        for p in previous.get("parts", [])
    ]
    published = _redis(
        lambda redis: redis.eval(
            "if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end; "
            "local count = tonumber(ARGV[4]); "
            "for i = 3, count + 2 do "
            "if redis.call('EXISTS', KEYS[i]) == 0 then return 0 end end; "
            "for i = 3, count + 2 do "
            "redis.call('EXPIRE', KEYS[i], tonumber(ARGV[3]) + 60) end; "
            "redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3]); "
            "for i = count + 3, #KEYS do redis.call('EXPIRE', KEYS[i], ARGV[5]) end; "
            "return 1",
            2 + len(new_keys) + len(old_keys),
            prefix + ":lock",
            prefix + ":manifest",
            *new_keys,
            *old_keys,
            token,
            json.dumps(manifest, separators=(",", ":")),
            ttl,
            len(new_keys),
            PREVIOUS_SECONDS,
        )
    )
    if not published:
        raise SitemapUnavailable("Sitemap refresh lease expired")
    return manifest


def refresh_snapshot():
    """Scheduled work only: publish a complete inventory without blocking crawlers."""
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
            return None
        # Another owner can finish between our first read and lease acquisition.
        current = _read(prefix + ":manifest")
        if current and current.get("format") == SNAPSHOT_FORMAT:
            previous = current
            if time.time() - current["built_at"] < refresh:
                return current
        return build_snapshot(prefix, token, get_settings().sitemap_retention_seconds)
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


def manifest():
    """Serve the retained snapshot even when a scheduled refresh is due or failed."""
    try:
        current = _read(_prefix() + ":manifest")
    except CacheUnavailable as exc:
        raise SitemapUnavailable("Sitemap is temporarily unavailable") from exc
    if current and current.get("format") == SNAPSHOT_FORMAT:
        return current
    raise SitemapUnavailable("Sitemap is being prepared")


def part(kind, page, generation=None):
    if generation is None:
        generation = manifest()["generation"]
    try:
        return _read(f"{_prefix()}:{generation}:{kind}:{page}")
    except CacheUnavailable as exc:
        raise SitemapUnavailable("Sitemap is temporarily unavailable") from exc
