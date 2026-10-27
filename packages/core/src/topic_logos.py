"""Topic-specific entry points into the shared managed logo pipeline."""

import uuid

from sqlalchemy.orm import Session

from devfeed_core.logos import backfill_logos, request_logo
from devfeed_core.models import ArticleImageJob, Topic


def request_topic_logo(
    session: Session, topic_id: uuid.UUID, *, automatic: bool = False
) -> ArticleImageJob | None:
    return request_logo(session, Topic, topic_id, automatic=automatic)


def backfill_topic_logos(session: Session, limit: int = 100) -> list[ArticleImageJob]:
    return backfill_logos(session, Topic, limit)
