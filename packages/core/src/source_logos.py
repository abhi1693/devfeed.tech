"""Publisher logo scheduling reuses the guarded, checkpointed logo pipeline."""

import uuid

from sqlalchemy.orm import Session

from devfeed_core.logos import backfill_logos, request_logo
from devfeed_core.models import ArticleImageJob, Source


def request_source_logo(
    session: Session, source_id: uuid.UUID, *, automatic: bool = False, refresh: bool = False
) -> ArticleImageJob | None:
    return request_logo(session, Source, source_id, automatic=automatic, refresh=refresh)


def backfill_source_logos(session: Session, limit: int = 100) -> list[ArticleImageJob]:
    return backfill_logos(session, Source, limit)
