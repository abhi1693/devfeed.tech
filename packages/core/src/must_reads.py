"""Shared visibility-safe reads of persisted daily Must Reads."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, raiseload

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.models import Article, ArticleOrigin, Source, UserMustRead, UserReadingEvent
from devfeed_core.publication import visible_article
from devfeed_core.user_settings import FeedSettings


def read_snapshot(
    session: Session,
    snapshot: UserMustRead | None,
    settings: FeedSettings,
    *,
    include_details: bool = True,
    lock_publication: bool = False,
) -> tuple[list[Article], dict[str, str], list[uuid.UUID]]:
    """Read a snapshot without creating picks or claiming their presentation."""
    if snapshot is None:
        return [], {}, []
    ids = [uuid.UUID(pick["id"]) for pick in snapshot.picks]
    if lock_publication:
        # This caller also creates/claims picks in its account transaction. Keep
        # that write connection and fence the small public projection in the same
        # Source -> Article order used by maintenance promotion and withdrawal.
        session.scalars(
            select(Source.id)
            .where(
                Source.id.in_(
                    select(ArticleOrigin.source_id).where(ArticleOrigin.article_id.in_(ids))
                ),
                Source.approval_status == "approved",
            )
            .order_by(Source.id)
            .with_for_update(read=True, of=Source)
        ).all()
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
    if lock_publication:
        statement = (
            statement.order_by(Article.id)
            .with_for_update(read=True, of=Article)
            .execution_options(populate_existing=True)
        )
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
