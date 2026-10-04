"""Invalidate user responses after application writes from API, CLI or workers."""

import weakref

from sqlalchemy import Engine, event, inspect
from sqlalchemy.orm import Session

from devfeed_core.cache import invalidate_public_cache

PUBLIC_TABLES = frozenset(
    {
        "sources",
        "articles",
        "article_origins",
        "article_tags",
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
REASONS = "devfeed_public_cache_reasons"
PRIVATE_ARTICLES = "devfeed_private_articles"
SESSION_OPTION = "devfeed_cache_session"
TABLE_OPTION = "devfeed_cache_table"


class AppSession(Session):
    """Only application sessions participate; migrations and raw connections don't."""


def mark_dirty(session, table):
    session.info[DIRTY] = True
    session.info.setdefault(REASONS, set()).add(table)


@event.listens_for(AppSession, "before_flush")
def track_objects(session, flush_context, instances):
    for obj in session.new | session.deleted | session.dirty:
        state = inspect(obj)
        table = state.mapper.local_table.name
        if table not in PUBLIC_TABLES:
            continue
        if table in {
            "article_topics",
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
            mark_dirty(session, table)
        elif table == "sources":
            if any(state.attrs[field].history.has_changes() for field in SOURCE_FIELDS):
                mark_dirty(session, table)
        elif table == "articles":
            private = {"editorial_revision", "review_status", "classification_provenance"}
            if any(
                attr.history.has_changes()
                for key, attr in state.attrs.items()
                if key not in private
            ):
                mark_dirty(session, table)
        elif session.is_modified(obj, include_collections=True):
            mark_dirty(session, table)


@event.listens_for(AppSession, "do_orm_execute")
def track_statements(state):
    # Ingestion/image workers use INSERT ... ON CONFLICT and UPDATE ... RETURNING,
    # which do not appear in session.dirty or mapper flush events.
    if state.is_insert or state.is_update or state.is_delete:
        if getattr(state, "execution_options", {}).get("devfeed_private_write"):
            return
        table = getattr(state.statement, "table", None)
        table_name = getattr(table, "name", None)
        if table_name in PUBLIC_TABLES:
            # Do not invalidate merely because a write was attempted. Ignored
            # duplicates and conditional updates matching zero rows are no-ops.
            state.update_execution_options(
                **{SESSION_OPTION: weakref.ref(state.session), TABLE_OPTION: table_name}
            )


@event.listens_for(Engine, "after_cursor_execute")
def track_affected_rows(connection, cursor, statement, parameters, context, executemany):
    reference = context.execution_options.get(SESSION_OPTION)
    if reference is None or cursor.rowcount == 0:
        return
    # Psycopg reports affected/returned rows without consuming RETURNING results.
    # Unknown counts (-1) conservatively invalidate rather than risking stale data.
    session = reference()
    if session is not None:
        mark_dirty(session, context.execution_options[TABLE_OPTION])


@event.listens_for(AppSession, "after_commit")
def committed(session):
    if not session.in_nested_transaction():
        session.info.pop(PRIVATE_ARTICLES, None)
        reasons = session.info.pop(REASONS, ())
        if session.info.pop(DIRTY, False):
            invalidate_public_cache(reasons=reasons)


@event.listens_for(AppSession, "after_soft_rollback")
def rolled_back(session, previous_transaction):
    if previous_transaction.parent is None:
        session.info.pop(DIRTY, None)
        session.info.pop(REASONS, None)
        session.info.pop(PRIVATE_ARTICLES, None)
