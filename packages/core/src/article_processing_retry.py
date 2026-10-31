"""Explicit processing retries preserve independent work and editorial authority."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, lazyload

from devfeed_core.ai_content import eligible_article
from devfeed_core.article_jobs import approved_sources, request_article_enrichment
from devfeed_core.article_lifecycle import (
    ProcessingFacts,
    ProcessingInputs,
    ProcessingReadiness,
    _processing_facts,
    derive_processing_readiness,
    derive_publication_readiness,
)
from devfeed_core.config import Settings, get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleRefresh,
    ArticleReview,
)
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.urls import validate_public_url

if TYPE_CHECKING:
    from devfeed_core.analysis import AnalysisCatalog

RetryTarget = Literal["enrichment", "analysis", "images"]
RETRY_TARGETS: tuple[RetryTarget, ...] = ("enrichment", "analysis", "images")
_positions = {
    stage: index
    for index, stage in enumerate(
        ("ingested", "enrichment", "analysis", "images", "review", "ready", "published", "rejected")
    )
}


def retry_readiness(
    article: Article,
    readiness: ProcessingReadiness,
    facts: ProcessingFacts,
    *,
    taxonomy: AnalysisCatalog | None = None,
    settings: Settings | None = None,
) -> ProcessingReadiness:
    """Preview prerequisites for published processing without changing its stage."""
    if article.publication_status != "published":
        return readiness
    inputs: ProcessingInputs = {
        **facts,
        "taxonomy": taxonomy or facts.get("taxonomy"),
        "settings": settings,
        "allow_published": True,
    }
    return derive_processing_readiness(article, **inputs)


def current_processing_job(
    facts: ProcessingFacts, target: RetryTarget, *, readiness: ProcessingReadiness | None = None
) -> ArticleAnalysisJob | ArticleEnrichmentJob | ArticleImageJob | None:
    if target == "enrichment":
        return facts["enrichment"]
    if target == "analysis":
        return facts["analysis"]
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


def processing_retry_targets(
    article: Article,
    readiness: ProcessingReadiness,
    facts: ProcessingFacts,
    *,
    settings: Settings | None = None,
) -> list[RetryTarget]:
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
    session: Session,
    identifier: uuid.UUID,
    *,
    target: RetryTarget,
    expected_revision: int,
    expected_job_id: uuid.UUID | None,
    actor: dict[str, str],
) -> Article:
    from devfeed_core.analysis import request_analysis
    from devfeed_core.image_jobs import request_image, retry_image
    from devfeed_core.job_retries import retry_candidate, retry_failed_job

    preview = session.scalar(select(Article).options(lazyload("*")).where(Article.id == identifier))
    if preview is not None and preview.publication_status == "published":
        from devfeed_core.analysis import reuse_candidates
        from devfeed_core.catalog_cache import reuse_snapshots

        with reuse_snapshots(), reuse_candidates():
            return _request_published_retry(
                session,
                preview,
                target=target,
                expected_revision=expected_revision,
                expected_job_id=expected_job_id,
                actor=actor,
            )
    approved_sources(session, identifier, lock=True)
    article = session.scalar(
        select(Article)
        .where(Article.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if article is None:
        raise RecordNotFound("Article not found")
    if article.editorial_revision != expected_revision:
        raise OperationConflict("Article changed; reload before retrying processing")
    if article.publication_status == "published":
        raise OperationConflict("Publication changed; reload before requesting maintenance")
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
    if eligible_failed and previous is not None and target == "images":
        job = retry_image(session, previous.id, refresh=True)
        if job is None:
            # A completed thumbnail-layout upgrade can make its specialized
            # checkpoint obsolete; the explicit image refresh must still run.
            job = request_image(session, identifier, refresh=True)
    elif eligible_failed and previous is not None:
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


def _request_published_retry(
    session: Session,
    preview: Article,
    *,
    target: RetryTarget,
    expected_revision: int,
    expected_job_id: uuid.UUID | None,
    actor: dict[str, str],
) -> Article:
    from devfeed_core.article_refresh import (
        ACTIVE_REFRESH_STATES,
        create_refresh,
        prepare_refresh_base,
    )
    from devfeed_core.article_refresh_processing import (
        prepare_initial_refresh_inputs,
        prepare_refresh_inputs,
        request_refresh_analysis,
    )
    from devfeed_core.image_jobs import current_refresh_image, request_image, retry_image

    if not get_settings().published_article_refresh_enabled:
        raise OperationConflict(
            "Published article maintenance is disabled until workers are upgraded"
        )
    if preview.editorial_revision != expected_revision:
        raise OperationConflict("Article changed; reload before retrying maintenance")
    ordinary = _processing_facts(session, preview, None)
    base = prepare_refresh_base(session, preview, ordinary)
    existing = session.scalar(
        select(ArticleRefresh).where(
            ArticleRefresh.article_id == preview.id,
            ArticleRefresh.status.in_(ACTIVE_REFRESH_STATES),
        )
    )
    inputs = (
        prepare_refresh_inputs(session, existing.id)
        if existing is not None
        else prepare_initial_refresh_inputs(
            session, preview, base, ordinary, taxonomy=ordinary.get("taxonomy")
        )
    )
    scopes = {
        "enrichment": RETRY_TARGETS,
        "analysis": ("analysis", "images"),
        "images": ("images",),
    }
    if existing is not None and target not in scopes[existing.target]:
        raise OperationConflict(
            "Another maintenance target is active; inspect it before replacing it"
        )
    facts = inputs.facts if existing is not None else ordinary
    subject = inputs.article if existing is not None else preview
    private_inputs: ProcessingInputs = {**facts, "taxonomy": inputs.taxonomy}
    original = derive_publication_readiness(subject, **private_inputs)
    readiness = retry_readiness(subject, original, facts, taxonomy=inputs.taxonomy)
    previous = current_processing_job(facts, target, readiness=readiness)
    if (previous.id if previous is not None else None) != expected_job_id:
        raise OperationConflict("Processing changed; reload before retrying this task")
    if target not in processing_retry_targets(subject, readiness, facts):
        raise OperationConflict("This maintenance task cannot be retried from its current state")
    if not approved_sources(session, preview.id, lock=True):
        raise OperationConflict("Article maintenance requires an approved source origin")
    article = session.scalar(
        select(Article)
        .options(lazyload("*"))
        .where(Article.id == preview.id)
        .with_for_update(of=Article)
        .execution_options(populate_existing=True)
    )
    if article is None:
        raise RecordNotFound("Article not found")
    refresh = create_refresh(
        session, article, base, cast(RetryTarget, existing.target) if existing else target, actor
    )
    if refresh.version != inputs.version:
        raise OperationConflict("Maintenance changed; reload before retrying this task")
    job: ArticleAnalysisJob | ArticleEnrichmentJob | ArticleImageJob | None
    if target == "analysis":
        job = request_refresh_analysis(session, refresh.id, prepared=inputs)
        if isinstance(previous, ArticleAnalysisJob) and previous.status == "failed":
            from devfeed_core.job_retries import preserve_analysis_steps

            preserve_analysis_steps(previous, job)
    elif target == "enrichment":
        job = request_article_enrichment(session, article.id, refresh_id=refresh.id)
    else:
        job = (
            retry_image(session, previous.id, refresh=True)
            if isinstance(previous, ArticleImageJob)
            and previous.status == "failed"
            and previous.refresh_id == refresh.id
            and current_refresh_image(session, article, previous) is not None
            else None
        )
        if job is None:
            job = request_image(session, article.id, refresh=True, refresh_id=refresh.id)
    if job is None:
        raise OperationConflict("No maintenance task could be queued")
    actor_name = (
        actor.get("name") or actor.get("email") or actor.get("subject") or "Administrator"
    )[:200]
    session.add(
        ArticleReview(
            article_id=article.id,
            action="retry",
            actor=actor_name,
            note=f"Retry {target} privately while preserving publication",
            revision=article.editorial_revision,
            automation={
                "kind": "processing_retry",
                "target": target,
                "refresh_id": str(refresh.id),
                "job_id": str(job.id),
                "previous_job_id": str(previous.id) if previous is not None else None,
                "actor": actor,
            },
        )
    )
    session.flush()
    return article
