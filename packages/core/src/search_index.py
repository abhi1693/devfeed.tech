"""Retryable Typesense projection with bounded fanout and a single writer lease."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, literal, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import Select

from devfeed_core.cache import invalidate_public_cache, invalidate_search_cache
from devfeed_core.catalog_cache import snapshot
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    SearchEvent,
    Source,
    Topic,
)
from devfeed_core.publication import visible_article
from devfeed_core.search_engine import KINDS, Typesense
from devfeed_core.search_records import LINKS, MODELS, documents
from devfeed_core.telemetry import Instruments

# Serialize remote index writes without locking application rows. A crash releases
# this transaction-level advisory lock and leaves all unacknowledged events intact.
INDEX_LOCK = 484530894367
logger = logging.getLogger(__name__)


def _metric_instruments() -> Instruments | None:
    from devfeed_core.telemetry import current

    runtime = current()
    return runtime.instruments if runtime else None


def _visible_tag_count_query() -> Select[int]:
    # Deduplicate destination UUIDs before counting. A tag EXISTS semi-join can
    # hash every matching assignment and spill hundreds of thousands of rows.
    visible_tags = (
        select(ArticleTag.tag_id)
        .join(Article, Article.id == ArticleTag.article_id)
        .where(visible_article())
        .distinct()
        .subquery()
    )
    return select(func.count()).select_from(visible_tags)


def _visible_tag_count(session: Session) -> int | None:
    # Revisions are changed in the same transaction as every relevant SQL write.
    # Redis expiry bounds storage, never decides publication/approval freshness.
    return snapshot(
        session, ("search_tag_visibility",), lambda: session.scalar(_visible_tag_count_query())
    )


def _visible_topic_count_query() -> Select[int]:
    # Keep the early-exit query for cache misses and cache-disabled deployments.
    return (
        select(func.count())
        .select_from(Topic)
        .where(
            Topic.status == "active",
            select(1)
            .select_from(ArticleTopic)
            .join(Article, Article.id == ArticleTopic.article_id)
            .where(
                ArticleTopic.topic_id == Topic.id,
                ArticleTopic.role.in_(["primary", "supporting"]),
                visible_article(),
            )
            .limit(1)
            .correlate(Topic)
            .scalar_subquery()
            .is_not(None),
        )
    )


def _visible_topic_count(session: Session) -> int | None:
    return snapshot(
        session,
        ("search_tag_visibility", "topics"),
        lambda: session.scalar(_visible_topic_count_query()),
    )


def _visible_counts(session: Session) -> dict[str, int]:
    counts = {
        "articles": session.scalar(
            select(func.count()).select_from(Article).where(visible_article())
        ),
        "topics": _visible_topic_count(session),
        "sources": session.scalar(
            select(func.count())
            .select_from(Source)
            .where(
                Source.approval_status == "approved",
                Source.enabled.is_(True),
                select(1)
                .select_from(ArticleOrigin)
                .join(Article, Article.id == ArticleOrigin.article_id)
                .where(ArticleOrigin.source_id == Source.id, visible_article())
                .exists(),
            )
        ),
        "tags": _visible_tag_count(session),
    }
    return {kind: int(value or 0) for kind, value in counts.items()}


def _update_outbox_metrics(session: Session) -> None:
    metrics = _metric_instruments()
    if not metrics:
        return
    depth, oldest = session.execute(
        select(func.count(SearchEvent.id), func.min(SearchEvent.created_at))
    ).one()
    age = max(0.0, (datetime.now(UTC) - oldest).total_seconds()) if oldest else 0.0
    metrics["search_outbox"].set(depth or 0)
    metrics["search_oldest"].set(age)


def reconcile(factory: sessionmaker[Session], engine: Typesense | None = None) -> dict[str, int]:
    """Refresh durable outbox and projection counts without holding DB connections remotely."""
    engine = engine or Typesense(admin=True)
    with factory() as session:
        _update_outbox_metrics(session)
        visible = _visible_counts(session)
    for kind in KINDS:
        remote = engine.count(kind)
        logger.info(
            "search_index_reconciled",
            extra={
                "collection": engine.collection(kind),
                "typesense_count": remote,
                "postgres_visible_count": visible[kind],
                "count_delta": remote - visible[kind],
            },
        )
    return visible


def backfill(factory: sessionmaker[Session]) -> int:
    count = 0
    with factory.begin() as session:
        for kind, model in MODELS.items():
            queued = (
                insert(SearchEvent)
                .from_select(
                    ["kind", "entity_id", "public_change"],
                    select(literal(kind), model.id, literal(False)),
                )
                .returning(SearchEvent.id)
                .cte("queued")
            )
            count += session.scalar(select(func.count()).select_from(queued)) or 0
    # Backfill may repair direct SQL writes whose triggers were disabled. Fence
    # public data once after the enqueue commit, rather than once per page.
    invalidate_public_cache(reasons=("search_index",))
    return count


def sync_batch(factory: sessionmaker[Session], engine: Typesense | None = None) -> int:
    engine = engine or Typesense(admin=True)
    batch = get_settings().search_index_batch_size
    with factory.begin() as session:
        if not session.scalar(select(text(f"pg_try_advisory_xact_lock({INDEX_LOCK})"))):
            return 0
        events = session.scalars(select(SearchEvent).order_by(SearchEvent.id).limit(batch)).all()
        if not events:
            return 0
        public_change = any(event.public_change is not False for event in events)
        for kind in KINDS:
            ids = {event.entity_id for event in events if event.kind == kind}
            if ids:
                current, removed = documents(session, kind, ids)
                engine.sync(kind, current, removed)
        # Origin visibility can change without an article UPDATE (e.g. source
        # rejection/deletion). Refresh its surviving catalogue destinations too.
        article_ids = {event.entity_id for event in events if event.kind == "articles"}
        if article_ids:
            for kind, (link, foreign) in LINKS.items():
                session.execute(
                    insert(SearchEvent).from_select(
                        ["kind", "entity_id", "public_change"],
                        select(literal(kind), foreign, literal(False))
                        .where(link.article_id.in_(article_ids))
                        .distinct(),
                    )
                )
        fanouts = {
            (event.kind, event.entity_id, event.after_id)
            for event in events
            if event.fanout and event.kind in LINKS
        }
        for kind, identifier, cursor in fanouts:
            link, foreign = LINKS[kind]
            statement = (
                select(link.article_id)
                .where(foreign == identifier)
                .order_by(link.article_id)
                .limit(batch)
            )
            if cursor:
                statement = statement.where(link.article_id > cursor)
            related_ids = session.scalars(statement).all()
            if related_ids:
                session.execute(
                    insert(SearchEvent),
                    [
                        {"kind": "articles", "entity_id": value, "public_change": False}
                        for value in related_ids
                    ],
                )
            if len(related_ids) == batch:
                session.add(
                    SearchEvent(
                        kind=kind,
                        entity_id=identifier,
                        fanout=True,
                        after_id=related_ids[-1],
                        public_change=False,
                    )
                )
        # Only acknowledge this batch. Changes committed during an HTTP import
        # retain their own event IDs and will overwrite any older projection.
        session.execute(
            delete(SearchEvent).where(SearchEvent.id.in_([event.id for event in events]))
        )
    invalidate_search_cache(public_change=public_change)
    return len(events)
