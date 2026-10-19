"""Explicit processing retries preserve independent work and editorial authority."""

import uuid
from typing import Literal

from sqlalchemy import select

from devfeed_core.ai_content import eligible_article
from devfeed_core.article_jobs import approved_sources, request_article_enrichment
from devfeed_core.article_lifecycle import (
    ProcessingReadiness,
    _processing_facts,
    derive_processing_readiness,
    derive_publication_readiness,
)
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleReview,
)
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.urls import validate_public_url

RetryTarget = Literal["enrichment", "analysis", "images"]
RETRY_TARGETS: tuple[RetryTarget, ...] = ("enrichment", "analysis", "images")
_positions = {
    stage: index
    for index, stage in enumerate(
        ("ingested", "enrichment", "analysis", "images", "review", "ready", "published", "rejected")
    )
}


def retry_readiness(article, readiness, facts, *, taxonomy=None, settings=None):
    """Preview prerequisites for published processing without changing its stage."""
    if article.publication_status != "published":
        return readiness
    return derive_processing_readiness(
        article,
        **{**facts, "taxonomy": taxonomy or facts.get("taxonomy"), "settings": settings},
        allow_published=True,
    )


def current_processing_job(facts, target: RetryTarget, *, readiness=None):
    if target != "images":
        return facts[target]
    images = [job for job in (facts["discovery"], facts["storage"]) if job is not None]
    if readiness is not None and "images" in readiness.checks:
        identifier = readiness.checks["images"].job_id
        current = next((job for job in images if job.id == identifier), None)
        if current is not None:
            return current
    return max(
        images,
        key=lambda job: (job.status in {"queued", "running"}, job.created_at, job.id),
        default=None,
    )


def processing_retry_targets(article, readiness: ProcessingReadiness, facts, *, settings=None):
    """Pure action eligibility; cards and locked mutation use the same facts."""
    settings = settings or get_settings()
    if article.review_status == "rejected" or not any(
        origin.source.approval_status == "approved" for origin in article.origins
    ):
        return []
    try:
        validate_public_url(article.canonical_url)
    except ValueError:
        return []
    published = article.publication_status == "published"
    if published and (
        not eligible_article(article)
        or not settings.ai_enabled
        and readiness.checks["analysis"].status != "complete"
    ):
        return []
    position = _positions["published" if published else readiness.stage]
    result: list[RetryTarget] = []
    for target in RETRY_TARGETS:
        job = current_processing_job(facts, target, readiness=readiness)
        if job is not None and job.status in {"queued", "running"}:
            continue  # Keep existing leases and provider retry delays intact.
        if _positions[target] > position or (
            _positions[target] == position
            and not (
                readiness.state == "blocked"
                and job is not None
                and job.status in {"failed", "succeeded"}
            )
        ):
            continue
        if target in {"analysis", "images"} and (
            readiness.checks["enrichment"].status != "complete"
            or {"paywalled_content", "insufficient_source_text"}.intersection(readiness.blockers)
        ):
            continue
        if target == "analysis" and (not settings.ai_enabled or not eligible_article(article)):
            continue
        if target == "images" and (
            readiness.checks["analysis"].status != "complete"
            or any(
                reason in readiness.blockers
                for reason in (
                    "content_date_deferred",
                    "missing_ai_summary",
                    "missing_ai_description",
                    "unidentifiable_title",
                    "manual_analysis_review_required",
                )
            )
        ):
            continue
        result.append(target)
    return result


def request_processing_retry(
    session,
    identifier: uuid.UUID,
    *,
    target: RetryTarget,
    expected_revision: int,
    expected_job_id: uuid.UUID | None,
    actor: dict,
):
    from devfeed_core.analysis import request_analysis
    from devfeed_core.editorial import EditorialDecision, decide_article
    from devfeed_core.image_jobs import request_image, retry_image
    from devfeed_core.job_retries import retry_candidate, retry_failed_job

    approved_sources(session, identifier, lock=True)
    article = session.scalar(select(Article).where(Article.id == identifier).with_for_update())
    if article is None:
        raise RecordNotFound("Article not found")
    if article.editorial_revision != expected_revision:
        raise OperationConflict("Article changed; reload before retrying processing")
    facts = _processing_facts(session, article, None)
    original = derive_publication_readiness(article, **facts)
    readiness = retry_readiness(article, original, facts)
    previous = current_processing_job(facts, target, readiness=readiness)
    if (previous.id if previous is not None else None) != expected_job_id:
        raise OperationConflict("Processing changed; reload before retrying this task")
    if target not in processing_retry_targets(article, readiness, facts):
        raise OperationConflict(
            "This processing target cannot be retried from the current article state"
        )
    metadata = {
        "kind": "processing_retry",
        "target": target,
        "from_stage": original.stage,
        "previous_job_id": str(previous.id) if previous is not None else None,
        "actor": actor,
    }
    actor_name = (
        actor.get("name") or actor.get("email") or actor.get("subject") or "Administrator"
    )[:200]
    if article.publication_status == "published":
        decide_article(
            session,
            identifier,
            EditorialDecision(
                action="unpublish",
                actor=actor_name,
                note=f"Unpublish to retry {target}",
                expected_revision=expected_revision,
            ),
            automation=metadata,
        )
    eligible_failed = (
        previous is not None
        and previous.status == "failed"
        and session.scalar(
            select(type(previous).id).where(
                type(previous).id == previous.id, retry_candidate(type(previous))
            )
        )
    )
    job: ArticleAnalysisJob | ArticleEnrichmentJob | ArticleImageJob | None
    if eligible_failed and target == "images":
        job = retry_image(session, previous.id, refresh=True)
        if job is None:
            # A completed thumbnail-layout upgrade can make its specialized
            # checkpoint obsolete; the explicit image refresh must still run.
            job = request_image(session, identifier, refresh=True)
    elif eligible_failed:
        job = retry_failed_job(
            session, previous, "article-enrichment" if target == "enrichment" else target, actor
        )
    elif target == "enrichment":
        job = request_article_enrichment(session, identifier)
    elif target == "analysis":
        job = request_analysis(session, identifier)
    else:
        job = request_image(session, identifier, refresh=True)
    if job is None:
        raise OperationConflict("No processing task could be queued")
    session.add(
        ArticleReview(
            article_id=identifier,
            action="retry",
            actor=actor_name,
            note=f"Retry {target} from {original.stage}",
            revision=article.editorial_revision,
            automation={**metadata, "job_id": str(job.id)},
        )
    )
    session.flush()
    return article
