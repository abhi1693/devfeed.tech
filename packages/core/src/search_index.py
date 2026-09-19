"""Retryable Typesense projection with bounded fanout and a single writer lease."""

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, literal, select, text

from devfeed_core.cache import invalidate_public_cache
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTopic,
    SearchEvent,
    Source,
    Tag,
    Topic,
)
from devfeed_core.publication import visible_article
from devfeed_core.search_engine import KINDS, Typesense
from devfeed_core.search_records import LINKS, MODELS, documents

# Serialize remote index writes without locking application rows. A crash releases
# this transaction-level advisory lock and leaves all unacknowledged events intact.
INDEX_LOCK = 484530894367
logger = logging.getLogger(__name__)


def _runtime_metrics():
    from devfeed_core.telemetry import current

    runtime = current()
    return runtime.metrics if runtime else None


def _visible_counts(session):
    counts = {
        "articles": session.scalar(
            select(func.count()).select_from(Article).where(visible_article())
        ),
        "topics": session.scalar(
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
                .exists(),
            )
        ),
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
        "tags": session.scalar(select(func.count()).select_from(Tag)),
    }
    return {kind: int(value or 0) for kind, value in counts.items()}


def _update_outbox_metrics(session, *, service):
    metrics = _runtime_metrics()
    if not metrics:
        return
    depth, oldest = session.execute(
        select(func.count(SearchEvent.id), func.min(SearchEvent.created_at))
    ).one()
    age = max(0.0, (datetime.now(UTC) - oldest).total_seconds()) if oldest else 0.0
    metrics.search_outbox_depth.labels(service).set(depth or 0)
    metrics.search_outbox_oldest_age.labels(service).set(age)


def reconcile(factory, engine=None):
    """Refresh durable outbox and projection counts without holding DB connections remotely."""
    engine = engine or Typesense(admin=True)
    service = "search-indexer"
    with factory() as session:
        _update_outbox_metrics(session, service=service)
        visible = _visible_counts(session)
    metrics = _runtime_metrics()
    for kind in KINDS:
        remote = engine.count(kind)
        if metrics:
            metrics.search_collection_documents.labels(service, kind, "typesense").set(remote)
            metrics.search_collection_documents.labels(service, kind, "postgres_visible").set(
                visible[kind]
            )
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


def backfill(factory):
    count = 0
    with factory.begin() as session:
        for kind, model in MODELS.items():
            queued = (
                insert(SearchEvent)
                .from_select(["kind", "entity_id"], select(literal(kind), model.id))
                .returning(SearchEvent.id)
                .cte("queued")
            )
            count += session.scalar(select(func.count()).select_from(queued))
    return count


def sync_batch(factory, engine=None):
    engine = engine or Typesense(admin=True)
    batch = get_settings().search_index_batch_size
    with factory.begin() as session:
        if not session.scalar(select(text(f"pg_try_advisory_xact_lock({INDEX_LOCK})"))):
            return 0
        events = session.scalars(select(SearchEvent).order_by(SearchEvent.id).limit(batch)).all()
        if not events:
            return 0
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
                        ["kind", "entity_id"],
                        select(literal(kind), foreign)
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
            ids = session.scalars(statement).all()
            if ids:
                session.execute(
                    insert(SearchEvent), [{"kind": "articles", "entity_id": value} for value in ids]
                )
            if len(ids) == batch:
                session.add(
                    SearchEvent(kind=kind, entity_id=identifier, fanout=True, after_id=ids[-1])
                )
        # Only acknowledge this batch. Changes committed during an HTTP import
        # retain their own event IDs and will overwrite any older projection.
        session.execute(
            delete(SearchEvent).where(SearchEvent.id.in_([event.id for event in events]))
        )
    invalidate_public_cache()
    return len(events)
