"""Restore supplied tags from retained feed evidence without fetching articles again."""

from collections import defaultdict
from uuid import UUID

from devfeed_core.db import session_factory
from devfeed_core.models import Article, ArticleOrigin, Source
from devfeed_core.source_tags import attach_source_tags, resolve_source_tags, source_tag_names
from sqlalchemy import func, select


def backfill_tags(
    *,
    limit: int = 100,
    after: UUID | None = None,
    source_id: UUID | None = None,
    dry_run: bool = False,
) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError("Limit must be between 1 and 500")
    statement = (
        select(
            ArticleOrigin.id,
            ArticleOrigin.article_id,
            ArticleOrigin.source_id,
            ArticleOrigin.source_metadata,
        )
        .join(Source)
        .where(
            Source.approval_status == "approved",
            func.jsonb_array_length(ArticleOrigin.source_metadata["tags"]) > 0,
        )
        .order_by(ArticleOrigin.id)
        .limit(limit)
    )
    if after is not None:
        statement = statement.where(ArticleOrigin.id > after)
    if source_id is not None:
        statement = statement.where(ArticleOrigin.source_id == source_id)
    with session_factory()() as session:
        rows = session.execute(statement).all()
        # Source review uses Source -> Article order, like ingestion.
        approved = set(
            session.scalars(
                select(Source.id)
                .where(
                    Source.id.in_({row.source_id for row in rows}),
                    Source.approval_status == "approved",
                )
                .order_by(Source.id)
                .with_for_update()
            ).all()
        )
        labels: dict[UUID, list[str]] = defaultdict(list)
        for row in rows:
            if row.source_id in approved:
                labels[row.article_id].extend(
                    source_tag_names(row.source_metadata.get("tags", [])[:30]).values()
                )
        resolved, created = resolve_source_tags(
            session, (name for names in labels.values() for name in names)
        )
        saved = 0
        for identifier in session.scalars(
            select(Article.id).where(Article.id.in_(labels)).order_by(Article.canonical_url)
        ):
            saved += attach_source_tags(
                session, identifier, (resolved[key] for key in source_tag_names(labels[identifier]))
            )
        result = {
            "origins_examined": len(rows),
            "tags_created": created,
            "links_saved": saved,
            "next_after": str(rows[-1].id) if len(rows) == limit else None,
            "dry_run": dry_run,
        }
        if dry_run:
            session.rollback()
        else:
            session.commit()
        return result
