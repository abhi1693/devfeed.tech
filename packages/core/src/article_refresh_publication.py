"""Prepare private publication evidence, then swap one complete approved projection."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, defer, lazyload, load_only, sessionmaker

from devfeed_core.analysis import (
    AnalysisResult,
    _ensure_primary_topic,
    replace_classifications,
    reuse_candidates,
)
from devfeed_core.article_jobs import approved_sources
from devfeed_core.article_lifecycle import ProcessingInputs, derive_publication_readiness
from devfeed_core.article_refresh import ACTIVE_REFRESH_STATES, approved_digest
from devfeed_core.article_refresh_processing import RefreshInputs, prepare_refresh_inputs
from devfeed_core.article_refresh_types import RefreshFields
from devfeed_core.catalog_cache import reuse_snapshots, snapshot_current
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleContent, ArticleRefresh, ArticleReview, utcnow
from devfeed_core.publication_policy import publication_source
from devfeed_core.services import OperationConflict
from devfeed_core.source_tags import attach_source_tags
from devfeed_core.topics import lock_topics


@dataclass(frozen=True)
class PreparedPromotion:
    inputs: RefreshInputs
    base_hash: str
    ready: bool
    reasons: tuple[str, ...]


def replacement_ready(inputs: RefreshInputs, target: str) -> tuple[bool, tuple[str, ...]]:
    """Pure staged-output checks do not grant source or editorial authority."""
    required = inputs.facts["enrichment" if target == "enrichment" else "analysis"]
    reasons = []
    if (
        required is None
        or required.refresh_id != inputs.refresh_id
        or required.status != "succeeded"
    ):
        reasons.append("maintenance_target_incomplete")
    analysis = inputs.facts["analysis"]
    if analysis is not None and analysis.refresh_id == inputs.refresh_id:
        if inputs.draft.analysis is None:
            reasons.append("private_analysis_output_missing")
        else:
            try:
                saved = AnalysisResult.model_validate(
                    {
                        name: analysis.result[name]
                        for name in AnalysisResult.model_fields
                        if name in (analysis.result or {})
                    }
                )
            except (ValidationError, ValueError):
                reasons.append("private_analysis_output_invalid")
            else:
                if saved != inputs.draft.analysis:
                    reasons.append("private_analysis_output_changed")
    current: ProcessingInputs = {
        **inputs.facts,
        "taxonomy": inputs.taxonomy,
        "settings": get_settings(),
    }
    readiness = derive_publication_readiness(inputs.article, **current)
    reasons.extend(readiness.blockers)
    return not reasons and readiness.stage == "ready", tuple(dict.fromkeys(reasons))


def prepare_promotion(session: Session, refresh_id: uuid.UUID) -> PreparedPromotion:
    inputs = prepare_refresh_inputs(session, refresh_id)
    owner = session.get(ArticleRefresh, refresh_id)
    assert owner is not None
    ready, reasons = replacement_ready(inputs, owner.target)
    return PreparedPromotion(inputs, owner.base_hash, ready, reasons)


def automatic_replacement_allowed(session: Session, article: Article) -> bool:
    full = get_settings().full_automation
    source = publication_source(article, full=full)
    if source is None or not (full or source.publication_policy == "auto"):
        return False
    return full or not bool(
        session.scalar(
            select(ArticleReview.id)
            .where(ArticleReview.article_id == article.id, ArticleReview.automation == {})
            .limit(1)
        )
    )


def commit_promotion(
    session: Session,
    prepared: PreparedPromotion,
    *,
    actor: dict[str, str] | None = None,
    note: str | None = None,
) -> ArticleRefresh:
    """Caller prepared every body/catalog calculation before entering this command."""
    inputs = prepared.inputs
    approved = approved_sources(session, inputs.public.id, lock=True)
    article = session.scalar(
        select(Article)
        .options(lazyload("*"))
        .where(Article.id == inputs.public.id)
        .with_for_update(of=Article)
        .execution_options(populate_existing=True)
    )
    owner = session.scalar(
        select(ArticleRefresh)
        .options(defer(ArticleRefresh.payload, raiseload=True))
        .where(ArticleRefresh.id == inputs.refresh_id)
        .with_for_update(of=ArticleRefresh)
        .execution_options(populate_existing=True)
    )
    if owner is None or owner.status not in ACTIVE_REFRESH_STATES:
        raise OperationConflict("This replacement is no longer active")
    if owner.version != inputs.version:
        raise OperationConflict("The replacement changed; reload before applying it")
    if (
        not approved
        or article is None
        or article.publication_status != "published"
        or article.review_status != "approved"
        or article.editorial_revision != owner.base_revision
        or owner.source_hash != inputs.input_hash
        or owner.base_hash != prepared.base_hash
        or approved_digest(session, article) != prepared.base_hash
    ):
        if actor is not None:
            raise OperationConflict("The approved publication or source authority changed")
        owner.status, owner.completed_at = "superseded", utcnow()
        owner.version += 1
        return owner
    lock_topics(session, read=True)
    if not snapshot_current(session, ("topics", "tags"), inputs.taxonomy):
        raise OperationConflict("The catalog changed; recheck the replacement before applying it")
    if not prepared.ready:
        raise OperationConflict(
            "Replacement processing is incomplete: " + ", ".join(prepared.reasons)
        )
    if actor is None and not automatic_replacement_allowed(session, article):
        if owner.status != "review":
            owner.status = "review"
            owner.version += 1
        return owner
    if actor is not None:
        owner.approved_version = owner.version
        owner.reviewed_by, owner.reviewed_at, owner.review_note = actor, utcnow(), note
    for name in RefreshFields.model_fields:
        setattr(article, name, getattr(inputs.article, name))
    article.image_url = inputs.article.image_url
    article.managed_image = dict(inputs.article.managed_image or {})
    attach_source_tags(
        session, article.id, [item.tag_id for item in inputs.draft.tags if item.origin == "source"]
    )
    if inputs.draft.analysis is not None:
        result = inputs.draft.analysis.model_copy(
            update={
                "topics": _ensure_primary_topic(
                    inputs.draft.analysis.developer_relevance, inputs.draft.analysis.topics
                )
            }
        )
        assigned = replace_classifications(session, article, result, origin="ai")
        article.classification_provenance = {**inputs.article.classification_provenance, **assigned}
    if inputs.draft.content is not None:
        content = session.get(
            ArticleContent,
            article.id,
            options=[
                load_only(
                    ArticleContent.article_id,
                    ArticleContent.content_hash,
                    ArticleContent.url,
                    ArticleContent.method,
                    ArticleContent.retrieved_at,
                    raiseload=True,
                )
            ],
        )
        if content is None:
            content = ArticleContent(article_id=article.id)
            session.add(content)
        if content.content_hash != inputs.draft.content.content_hash:
            content.text = inputs.draft.content.text
        content.content_hash = inputs.draft.content.content_hash
        content.url, content.method = inputs.draft.content.url, inputs.draft.content.method
        content.retrieved_at = inputs.draft.content.retrieved_at
    article.editorial_revision += 1
    owner.status, owner.completed_at = "promoted", utcnow()
    owner.version += 1
    session.add(
        ArticleReview(
            article_id=article.id,
            action="refresh",
            actor=(actor.get("name") or actor.get("subject") or "Administrator")[:200]
            if actor
            else "DevFeed maintenance",
            note=note or "Promote the complete private replacement",
            revision=article.editorial_revision,
            automation={
                "kind": "maintenance_promotion",
                "refresh_id": str(owner.id),
                "base_revision": owner.base_revision,
            },
        )
    )
    session.flush()
    return owner


def promote_processed_refresh(factory: sessionmaker[Session], refresh_id: uuid.UUID) -> str:
    with reuse_snapshots(reuse_existing=True), reuse_candidates(reuse_existing=True):
        with factory() as session:
            prepared = prepare_promotion(session, refresh_id)
        if not prepared.ready:
            return "pending"
        try:
            with factory.begin() as session:
                owner = commit_promotion(session, prepared)
                return owner.status
        except OperationConflict:
            return "pending"


def approve_refresh(
    session: Session,
    article_id: uuid.UUID,
    refresh_id: uuid.UUID,
    expected_version: int,
    actor: dict[str, str],
    note: str | None,
) -> ArticleRefresh:
    with reuse_snapshots(), reuse_candidates():
        prepared = prepare_promotion(session, refresh_id)
        if prepared.inputs.public.id != article_id or prepared.inputs.version != expected_version:
            raise OperationConflict("The replacement changed; reload before reviewing it")
        return commit_promotion(session, prepared, actor=actor, note=note)
