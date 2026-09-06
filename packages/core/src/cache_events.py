"""Invalidate reader responses after application writes from API, CLI or workers."""

import weakref

from sqlalchemy import Engine, event, inspect
from sqlalchemy.orm import Session

from devfeed_core.cache import invalidate_public_cache

PUBLIC_TABLES = frozenset(
    {
        "sources",
        "articles",
        "article_origins",
        "article_categories",
        "article_tags",
        "categories",
        "tags",
        "topics",
        "topic_relations",
        "article_topics",
    }
)
SOURCE_FIELDS = frozenset(
    {
        "name",
        "source_type",
        "description",
        "website_url",
        "logo_url",
        "image_url",
        "language",
        "approval_status",
        "enabled",
        "created_at",
    }
)
DIRTY = "devfeed_public_cache_dirty"
PRIVATE_ARTICLES = "devfeed_private_articles"
SESSION_OPTION = "devfeed_cache_session"


class AppSession(Session):
    """Only application sessions participate; migrations and raw connections don't."""


@event.listens_for(AppSession, "before_flush")
def track_objects(session, flush_context, instances):
    for obj in session.new | session.deleted | session.dirty:
        state = inspect(obj)
        table = state.mapper.local_table.name
        if table not in PUBLIC_TABLES:
            continue
        if table in {
            "article_topics",
            "article_categories",
            "article_tags",
        } and obj.article_id in session.info.get(PRIVATE_ARTICLES, set()):
            continue
        if table == "articles":
            visibility = state.attrs.publication_status
            known_private = (
                visibility.loaded_value == "unpublished"
                and "published" not in visibility.history.deleted
            )
            if known_private or (obj in session.new and visibility.loaded_value != "published"):
                continue
        if obj in session.new or obj in session.deleted:
            session.info[DIRTY] = True
        elif table == "sources":
            if any(state.attrs[field].history.has_changes() for field in SOURCE_FIELDS):
                session.info[DIRTY] = True
        elif table == "articles":
            private = {"editorial_revision", "review_status", "classification_provenance"}
            if any(
                attr.history.has_changes()
                for key, attr in state.attrs.items()
                if key not in private
            ):
                session.info[DIRTY] = True
        elif session.is_modified(obj, include_collections=True):
            session.info[DIRTY] = True


@event.listens_for(AppSession, "do_orm_execute")
def track_statements(state):
    # Ingestion/image workers use INSERT ... ON CONFLICT and UPDATE ... RETURNING,
    # which do not appear in session.dirty or mapper flush events.
    if state.is_insert or state.is_update or state.is_delete:
        if getattr(state, "execution_options", {}).get("devfeed_private_write"):
            return
        table = getattr(state.statement, "table", None)
        if getattr(table, "name", None) in PUBLIC_TABLES:
            # Do not invalidate merely because a write was attempted. Ignored
            # duplicates and conditional updates matching zero rows are no-ops.
            state.update_execution_options(**{SESSION_OPTION: weakref.ref(state.session)})


@event.listens_for(Engine, "after_cursor_execute")
def track_affected_rows(connection, cursor, statement, parameters, context, executemany):
    reference = context.execution_options.get(SESSION_OPTION)
    if reference is None or cursor.rowcount == 0:
        return
    # Psycopg reports affected/returned rows without consuming RETURNING results.
    # Unknown counts (-1) conservatively invalidate rather than risking stale data.
    session = reference()
    if session is not None:
        session.info[DIRTY] = True


@event.listens_for(AppSession, "after_commit")
def committed(session):
    if not session.in_nested_transaction():
        session.info.pop(PRIVATE_ARTICLES, None)
        if session.info.pop(DIRTY, False):
            invalidate_public_cache()


@event.listens_for(AppSession, "after_soft_rollback")
def rolled_back(session, previous_transaction):
    if previous_transaction.parent is None:
        session.info.pop(DIRTY, None)
        session.info.pop(PRIVATE_ARTICLES, None)
