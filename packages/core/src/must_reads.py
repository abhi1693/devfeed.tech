"""Shared visibility-safe reads of persisted daily Must Reads."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import raiseload

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.models import Article, UserReadingEvent
from devfeed_core.publication import visible_article


def read_snapshot(session, snapshot, settings, *, include_details=True):
    """Read a snapshot without creating picks or claiming their presentation."""
    if snapshot is None:
        return [], {}, []
    ids = [uuid.UUID(pick["id"]) for pick in snapshot.picks]
    statement = select(Article).where(
        Article.id.in_(ids),
        visible_article(),
        Article.language.in_(settings.languages),
        Article.content_type.in_(settings.content_types),
    )
    if include_details:
        statement = statement.options(*PUBLIC_ARTICLE_OPTIONS)
    else:
        statement = statement.options(raiseload("*"))
    by_id = {article.id: article for article in session.scalars(statement)}
    read_ids = list(
        session.scalars(
            select(UserReadingEvent.article_id)
            .where(
                UserReadingEvent.user_id == snapshot.user_id,
                UserReadingEvent.article_id.in_(list(by_id)),
            )
            .distinct()
        )
    )
    reasons = {
        pick["id"]: pick["reason"] for pick in snapshot.picks if uuid.UUID(pick["id"]) in by_id
    }
    return [by_id[id] for id in ids if id in by_id], reasons, read_ids
