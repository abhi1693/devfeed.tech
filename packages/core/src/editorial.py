"""Editorial policy. AI output is evidence, never authority to publish."""

import logging
import re
import uuid
from typing import Literal, get_args

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from devfeed_core.article_jobs import approved_sources
from devfeed_core.models import Article, ArticleReview, utcnow
from devfeed_core.schemas import ContentFormat, ContentType, InputModel, Name, ReviewNote
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.urls import validate_public_url
from devfeed_core.video_content import (
    automatic_video_evidence,
    has_video_description,
    verified_video_metadata,
    video_description,
    youtube_video,
)

logger = logging.getLogger(__name__)


class EditorialDecision(InputModel):
    action: Literal["approve", "reject", "publish", "unpublish"]
    actor: Name | None = None
    note: ReviewNote | None = None
    expected_revision: int | None = Field(default=None, ge=0)


def meaningful_text(value: str | None) -> bool:
    # Length does not establish relevance. The current analysis owns that decision;
    # only empty or non-textual input is unusable here.
    return any(character.isalpha() for character in (value or ""))


def publication_blockers(article: Article) -> list[str]:
    """Pure metadata policy, also exposed through the operator CLI."""
    reasons = []
    try:
        validate_public_url(article.canonical_url)
    except ValueError:
        reasons.append("invalid_canonical_url")
    if not article.title or not article.title.strip():
        reasons.append("missing_title")
    if article.content_type not in get_args(ContentType):
        reasons.append("unknown_content_type")
    if article.content_format not in get_args(ContentFormat):
        reasons.append("unknown_content_format")
    if (
        not article.language
        or not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", article.language)
        or article.language in {"und", "mul", "zxx"}
    ):
        reasons.append("unknown_language")
    if not meaningful_text(article.summary) and not meaningful_text(article.ai_summary):
        reasons.append("missing_summary")
    if not any(
        link.role == "primary" and link.topic.status == "active" for link in article.topic_links
    ):
        reasons.append("missing_active_primary_topic")
    if (article.classification_provenance or {}).get("developer_relevance") != "relevant":
        reasons.append("developer_relevance_unresolved")
    if youtube_video(article):
        if (article.classification_provenance or {}).get(
            "page_kind"
        ) != "video" or article.content_format != "video":
            reasons.append("not_substantive_video")
        if not verified_video_metadata(article):
            reasons.append("video_metadata_unavailable")
        if not has_video_description(article):
            reasons.append("insufficient_video_evidence")
        if (article.classification_provenance or {}).get("origin") != "manual":
            description = video_description(article)
            if not automatic_video_evidence(description, article.title):
                reasons.append("insufficient_video_evidence")
            if (
                article.ai_title
                or article.ai_description
                or (article.ai_summary and article.ai_summary not in description)
            ):
                reasons.append("ungrounded_video_presentation")
    elif (article.classification_provenance or {}).get("page_kind") != "article":
        reasons.append("not_substantive_article")
    if article.review_status != "approved":
        reasons.append("not_approved")
    return reasons


def invalidate_editorial(article: Article) -> None:
    """Source changes invalidate approval and generated prose, not a rejection."""
    article.editorial_revision = (article.editorial_revision or 0) + 1
    article.publication_status = "unpublished"
    if article.review_status != "rejected":
        article.review_status = "pending"
    article.ai_title = article.ai_summary = article.ai_description = None
    article.classification_provenance = {}


def decide_article(
    session: Session,
    identifier: uuid.UUID,
    body: EditorialDecision,
    *,
    dry_run=False,
    automation=None,
):
    article = session.scalar(
        select(Article).where(Article.id == identifier).with_for_update(of=Article)
    )
    if article is None:
        raise RecordNotFound("Article not found")
    if body.expected_revision is not None and article.editorial_revision != body.expected_revision:
        raise OperationConflict("Article changed; inspect it before repeating the decision")
    if body.action == "reject" and not body.note:
        raise OperationConflict("Rejecting an article requires a reason")
    blockers = publication_blockers(article)
    if not approved_sources(session, article.id):
        blockers.append("no_approved_source")
    if body.action == "approve":
        purpose = {"not_substantive_article", "not_substantive_video"}.intersection(blockers)
        if purpose:
            raise OperationConflict("Approval blocked: " + ", ".join(sorted(purpose)))
    if body.action == "publish" and blockers:
        raise OperationConflict("Publication blocked: " + ", ".join(blockers))
    if dry_run:
        return article
    decision_at = utcnow()
    if body.action in {"approve", "reject"}:
        article.review_status = "approved" if body.action == "approve" else "rejected"
    if body.action in {"reject", "unpublish"}:
        article.publication_status = "unpublished"
    elif body.action == "publish":
        article.publication_status = "published"
        if article.published_to_feed_at is None:
            article.published_to_feed_at = decision_at
            from devfeed_core.feed_notifications import record_publication

            record_publication(session, article, decision_at)
    article.editorial_revision = (article.editorial_revision or 0) + 1
    session.add(
        ArticleReview(
            article_id=article.id,
            action=body.action,
            actor=body.actor,
            note=body.note,
            revision=article.editorial_revision,
            automation=automation or {},
            created_at=decision_at,
        )
    )
    logger.info(
        "article_editorial_decision", extra={"article_id": article.id, "action": body.action}
    )
    return article
