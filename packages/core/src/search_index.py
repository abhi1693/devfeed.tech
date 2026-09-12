"""Retryable Typesense projection with bounded fanout and a single writer lease."""

from sqlalchemy import delete, func, insert, literal, select, text

from devfeed_core.cache import invalidate_public_cache
from devfeed_core.config import get_settings
from devfeed_core.models import SearchEvent
from devfeed_core.search_engine import KINDS, Typesense
from devfeed_core.search_records import LINKS, MODELS, documents

# Serialize remote index writes without locking application rows. A crash releases
# this transaction-level advisory lock and leaves all unacknowledged events intact.
INDEX_LOCK = 484530894367


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
