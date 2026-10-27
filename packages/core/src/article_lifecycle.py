"""Derived processing prerequisites shared by publication and operator views.

Jobs remain the durable execution state. This module does not persist another
workflow status or schedule work while rendering a readiness result.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict, Unpack, cast

from pydantic import BaseModel, Field
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session, defer

from devfeed_core.article_quality import presentation_blockers
from devfeed_core.config import Settings, get_settings
from devfeed_core.managed_images import IMAGE_VERSION
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleImageJob,
    utcnow,
)

if TYPE_CHECKING:
    from devfeed_core.analysis import AnalysisCatalog

LifecycleStage = Literal[
    "ingested", "enrichment", "analysis", "images", "review", "ready", "published", "rejected"
]
LifecycleState = Literal["waiting", "running", "retry_wait", "blocked", "ready", "complete"]
CheckStatus = Literal[
    "missing",
    "queued",
    "running",
    "retry_wait",
    "complete",
    "unavailable",
    "failed",
    "stale",
    "disabled",
]


class ProcessingFacts(TypedDict):
    content: ArticleContent | None
    taxonomy: NotRequired[AnalysisCatalog | None]
    enrichment: ArticleEnrichmentJob | None
    analysis: ArticleAnalysisJob | None
    discovery: ArticleImageJob | None
    storage: ArticleImageJob | None


class ProcessingInputs(TypedDict, total=False):
    content: ArticleContent | None
    taxonomy: AnalysisCatalog | None
    enrichment: ArticleEnrichmentJob | None
    analysis: ArticleAnalysisJob | None
    discovery: ArticleImageJob | None
    storage: ArticleImageJob | None
    settings: Settings | None
    now: datetime | None
    allow_published: bool


class ProcessingCheck(BaseModel):
    status: CheckStatus
    job_id: uuid.UUID | None = None
    outcome: str | None = None
    error: str | None = None
    available_at: datetime | None = None


class ProcessingReadiness(BaseModel):
    stage: LifecycleStage
    state: LifecycleState
    blockers: list[str] = Field(default_factory=list)
    checks: dict[str, ProcessingCheck] = Field(default_factory=dict)


def _check(
    job: ArticleAnalysisJob | ArticleEnrichmentJob | ArticleImageJob | None, now: datetime
) -> ProcessingCheck:
    if job is None:
        return ProcessingCheck(status="missing")
    status = job.status
    if status == "queued" and job.available_at > now and job.attempts:
        status = "retry_wait"
    return ProcessingCheck(
        status="complete" if status == "succeeded" else status,
        job_id=job.id,
        outcome=job.outcome,
        error=job.error,
        available_at=job.available_at,
    )


def _current_page(job: ArticleEnrichmentJob, article: Article) -> bool:
    evidence = job.result or {}
    return evidence.get("input_url", evidence.get("final_url")) == article.canonical_url


def _stored_image(article: Article, settings: Settings) -> bool:
    asset = article.managed_image or {}
    variants = asset.get("variants")
    return bool(
        settings.image_public_url
        and asset.get("source_url") == article.image_url
        and asset.get("version") == IMAGE_VERSION
        and isinstance(variants, list)
        and variants
        and all(
            isinstance(item, dict)
            and isinstance(item.get("key"), str)
            and item["key"]
            and isinstance(item.get("width"), int)
            and item["width"] > 0
            for item in variants
        )
    )


def derive_processing_readiness(
    article: Article,
    *,
    content: ArticleContent | None = None,
    enrichment: ArticleEnrichmentJob | None = None,
    analysis: ArticleAnalysisJob | None = None,
    discovery: ArticleImageJob | None = None,
    storage: ArticleImageJob | None = None,
    taxonomy: AnalysisCatalog | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    allow_published: bool = False,
) -> ProcessingReadiness:
    """Derive stages from preloaded facts; callers may share one catalog per batch."""
    if article.review_status == "rejected":
        return ProcessingReadiness(stage="rejected", state="complete")
    if article.publication_status == "published" and not allow_published:
        return ProcessingReadiness(stage="published", state="complete")
    from devfeed_core.ai_content import eligible_article
    from devfeed_core.analysis import (
        PROMPT_VERSION,
        analysis_catalog_current,
        snapshot_hash,
        source_snapshot,
    )

    settings, now = settings or get_settings(), now or utcnow()
    checks = {"enrichment": _check(enrichment, now), "analysis": _check(analysis, now)}
    groups: dict[str, list[str]] = {"enrichment": [], "analysis": [], "images": []}
    page = checks["enrichment"]
    if enrichment is None:
        groups["enrichment"].append("enrichment_required")
    elif page.status in {"queued", "running", "retry_wait"}:
        groups["enrichment"].append("enrichment_pending")
    elif page.status == "failed":
        groups["enrichment"].append("enrichment_failed")
    elif not _current_page(enrichment, article) or enrichment.outcome not in {
        "enriched",
        "metadata_only",
        "not_found",
    }:
        page.status = "stale"
        groups["enrichment"].append("current_enrichment_required")
    if enrichment is not None and (enrichment.result or {}).get("paywalled") is True:
        groups["enrichment"].append("paywalled_content")

    snapshot = source_snapshot(article, content)
    if not any(character.isalpha() for character in snapshot["text"] or ""):
        groups["enrichment"].append("insufficient_source_text")
    ai = checks["analysis"]
    if not eligible_article(article):
        groups["analysis"].append("content_date_deferred")
    if analysis is None:
        if not settings.ai_enabled:
            ai.status = "disabled"
            groups["analysis"].append("ai_not_configured")
        groups["analysis"].append("current_analysis_required")
    elif ai.status in {"queued", "running", "retry_wait"}:
        groups["analysis"].append("analysis_pending")
    elif ai.status == "failed":
        groups["analysis"].append("analysis_failed")
    elif (
        analysis.outcome == "insufficient_evidence"
        and analysis.input_hash == snapshot_hash(snapshot)
        and analysis.prompt_version == PROMPT_VERSION
        and taxonomy is not None
        and analysis_catalog_current(analysis, taxonomy, snapshot)
    ):
        ai.status = "unavailable"
        groups["analysis"].append("insufficient_analysis_evidence")
    else:
        provenance = article.classification_provenance or {}
        generation = provenance.get("ai_generation")
        if not isinstance(generation, dict):
            generation = provenance  # Existing generated rows use the top-level identity.
        if (
            analysis.outcome != "applied"
            or analysis.input_hash != snapshot_hash(snapshot)
            or generation.get("analysis_id") != str(analysis.id)
            or generation.get("input_hash") != analysis.input_hash
            or analysis.prompt_version != PROMPT_VERSION
            or (analysis.result or {}).get("outcome") != "ready"
        ):
            ai.status = "stale"
            groups["analysis"].append("current_analysis_required")
        elif taxonomy is None or not analysis_catalog_current(analysis, taxonomy, snapshot):
            ai.status = "stale"
            groups["analysis"].append("current_catalog_required")
    groups["analysis"].extend(presentation_blockers(article))
    if (article.classification_provenance or {}).get("origin") == "manual" and groups["analysis"]:
        groups["analysis"].append("manual_analysis_review_required")

    active_image = next(
        (
            job
            for job in (storage, discovery)
            if job is not None and job.status in {"queued", "running"}
        ),
        None,
    )
    image_job = active_image or (
        storage if article.image_url and settings.image_storage_enabled else discovery
    )
    current_failed_refresh = (
        discovery is not None
        and discovery.status == "failed"
        and (discovery.storage or {}).get("article_refresh") is True
        and (discovery.storage or {}).get("input_url") == article.canonical_url
        and (discovery.storage or {}).get("previous_image_url") == article.image_url
    )
    if (
        active_image is None
        and current_failed_refresh
        and discovery is not None
        and (
            image_job is None
            or (discovery.created_at, discovery.id) >= (image_job.created_at, image_job.id)
        )
    ):
        image_job = discovery
    image = checks["images"] = _check(image_job, now)
    if active_image is not None:
        groups["images"].append("image_processing_pending")
    elif (
        image_job is not None
        and image.status == "failed"
        and (
            (
                image_job.image_url == article.image_url
                if image_job.operation == "store"
                else (image_job.storage or {}).get("article_refresh") is True
                and (image_job.storage or {}).get("input_url") == article.canonical_url
                and (image_job.storage or {}).get("previous_image_url") == article.image_url
            )
            if article.image_url
            else (image_job.storage or {}).get("input_url") == article.canonical_url
        )
    ):
        groups["images"].append(
            "image_storage_failed" if image_job.operation == "store" else "image_discovery_failed"
        )
    elif article.image_url:
        if not settings.image_storage_enabled or _stored_image(article, settings):
            image.status = "complete"
        elif image_job is None or image_job.image_url != article.image_url:
            image.status = "missing" if image_job is None else "stale"
            groups["images"].append("image_storage_required")
        elif image.status in {"queued", "running", "retry_wait"}:
            groups["images"].append("image_storage_pending")
        elif image.status == "failed":
            groups["images"].append("image_storage_failed")
        else:
            image.status = "stale"
            groups["images"].append("image_storage_required")
    elif discovery is not None and image.status in {"queued", "running", "retry_wait", "failed"}:
        groups["images"].append(
            "image_discovery_failed" if image.status == "failed" else "image_discovery_pending"
        )
    elif (
        discovery is not None
        and discovery.status == "succeeded"
        and discovery.outcome == "not_found"
        and (discovery.storage or {}).get("input_url") == article.canonical_url
    ) or (
        page.status == "complete"
        and enrichment is not None
        and "image_url" in (enrichment.result or {})
        and not enrichment.result["image_url"]
    ):
        image.status = "unavailable"  # Completed lookup; the reader's existing fallback is valid.
    else:
        image.status = "missing" if discovery is None else "stale"
        groups["images"].append("image_discovery_required")

    blockers = [reason for reasons in groups.values() for reason in reasons]
    for step, reasons in groups.items():
        if reasons:
            check = checks[step]
            state: LifecycleState = (
                "running"
                if check.status == "running"
                else "retry_wait"
                if check.status == "retry_wait"
                else "waiting"
                if check.status in {"missing", "queued"}
                else "blocked"
            )
            return ProcessingReadiness(
                stage="ingested" if step == "enrichment" and enrichment is None else step,
                state=state,
                blockers=blockers,
                checks=checks,
            )
    return ProcessingReadiness(
        stage="ready" if article.review_status == "approved" else "review",
        state="ready" if article.review_status == "approved" else "waiting",
        checks=checks,
    )


def derive_publication_readiness(
    article: Article, **facts: Unpack[ProcessingInputs]
) -> ProcessingReadiness:
    result = derive_processing_readiness(article, **facts)
    if result.stage in {"published", "rejected"}:
        return result
    from devfeed_core.editorial import publication_blockers

    metadata = publication_blockers(article)
    if not any(origin.source.approval_status == "approved" for origin in article.origins):
        metadata.append("no_approved_source")
    result.blockers = list(dict.fromkeys([*result.blockers, *metadata]))
    if result.stage in {"ready", "review"} and metadata:
        result.stage = "review"
        result.state = "waiting" if metadata == ["not_approved"] else "blocked"
    return result


def _processing_facts(
    session: Session, article: Article, taxonomy: AnalysisCatalog | None
) -> ProcessingFacts:
    from devfeed_core.analysis import catalog

    facts: dict[str, object] = {
        "content": session.get(ArticleContent, article.id),
        "taxonomy": taxonomy,
    }
    for name, model, operation in (
        ("enrichment", ArticleEnrichmentJob, None),
        ("analysis", ArticleAnalysisJob, None),
        ("discovery", ArticleImageJob, "discover"),
        ("storage", ArticleImageJob, "store"),
    ):
        statement = select(model).where(model.article_id == article.id)
        if model is ArticleAnalysisJob:
            # Active jobs need only execution metadata. The retained input and
            # catalog evidence can be much larger than the article itself.
            statement = statement.options(
                defer(ArticleAnalysisJob.input_snapshot),
                defer(ArticleAnalysisJob.catalog_snapshot),
                defer(ArticleAnalysisJob.result),
                defer(ArticleAnalysisJob.usage),
            )
        if operation is not None:
            statement = statement.where(ArticleImageJob.operation == operation)
        current_image_input = (
            ArticleImageJob.image_url == article.image_url
            if operation == "store"
            else ArticleImageJob.storage["input_url"].as_string() == article.canonical_url
            if operation == "discover"
            else None
        )
        facts[name] = session.scalar(
            statement.order_by(
                model.status.in_(["queued", "running"]).desc(),
                *(
                    [func.coalesce(current_image_input, False).desc()]
                    if current_image_input is not None
                    else []
                ),
                model.created_at.desc(),
                model.id.desc(),
            ).limit(1)
        )
    analysis = cast(ArticleAnalysisJob | None, facts["analysis"])
    if analysis is not None and analysis.status not in {"queued", "running"}:
        details = {"catalog_snapshot", "result", "usage"} & inspect(analysis).unloaded
        if details:
            session.refresh(analysis, attribute_names=sorted(details))
    if facts["taxonomy"] is None and analysis is not None and analysis.status == "succeeded":
        facts["taxonomy"] = catalog(session)
    # Every fixed model lane above installs its key before this typed view escapes.
    return cast(ProcessingFacts, facts)


def processing_readiness(
    session: Session, article: Article, *, taxonomy: AnalysisCatalog | None = None
) -> ProcessingReadiness:
    if article.review_status == "rejected" or article.publication_status == "published":
        return derive_processing_readiness(article)
    return derive_processing_readiness(article, **_processing_facts(session, article, taxonomy))


def publication_readiness(
    session: Session, article: Article, *, taxonomy: AnalysisCatalog | None = None
) -> ProcessingReadiness:
    if article.review_status == "rejected" or article.publication_status == "published":
        return derive_publication_readiness(article)
    return derive_publication_readiness(article, **_processing_facts(session, article, taxonomy))


def reconcile_processing(
    session: Session, article: Article, *, taxonomy: AnalysisCatalog | None = None
) -> ProcessingReadiness:
    """Queue missing prerequisites once; terminal failures need explicit job retry.

    Callers hold source authority and the article row lock. No publication or
    external I/O happens here; the existing durable outboxes own delivery/retries.
    """
    if article.review_status == "rejected" or article.publication_status == "published":
        return derive_publication_readiness(article)
    from devfeed_core.ai_content import eligible_article
    from devfeed_core.analysis import (
        PROMPT_VERSION,
        analysis_catalog_current,
        request_analysis,
        snapshot_hash,
        source_snapshot,
    )
    from devfeed_core.article_jobs import request_article_enrichment
    from devfeed_core.image_jobs import request_image

    facts = _processing_facts(session, article, taxonomy)
    result = derive_processing_readiness(article, **facts)
    requested = False
    enrichment = facts["enrichment"]
    if enrichment is None:
        request_article_enrichment(session, article.id, automatic=True)
        requested = True
    elif result.checks["enrichment"].status == "stale":
        request_article_enrichment(session, article.id)
        requested = True
    if result.checks["images"].status in {"missing", "stale"} and (
        article.image_url or result.checks["enrichment"].status == "complete"
    ):
        storage = facts["storage"]
        request_image(
            session,
            article.id,
            automatic=not (
                storage is not None
                and storage.status == "succeeded"
                and result.checks["images"].status == "stale"
            ),
        )
        requested = True
    if (
        result.checks["enrichment"].status == "complete"
        and get_settings().ai_enabled
        and eligible_article(article)
        and "insufficient_source_text" not in result.blockers
        and (article.classification_provenance or {}).get("origin") != "manual"
    ):
        job = facts["analysis"]
        snapshot = source_snapshot(article, facts["content"])
        if job is None or (
            job.status == "succeeded"
            and (
                job.input_hash != snapshot_hash(snapshot)
                or job.prompt_version != PROMPT_VERSION
                or job.outcome not in {"applied", "insufficient_evidence"}
                or not analysis_catalog_current(
                    job, cast("AnalysisCatalog", facts["taxonomy"]), snapshot
                )
                or (
                    job.outcome == "applied"
                    and any(
                        reason in result.blockers
                        for reason in (
                            "current_analysis_required",
                            "unidentifiable_title",
                            "missing_ai_summary",
                            "missing_ai_description",
                        )
                    )
                )
            )
        ):
            request_analysis(
                session,
                article.id,
                automatic=True,
                force=job is not None,
                taxonomy=facts["taxonomy"],
            )
            requested = True
    session.flush()
    if requested:
        return publication_readiness(session, article, taxonomy=facts["taxonomy"])
    # Rechecking unchanged durable work must not hydrate its history/evidence a
    # second time. Refresh facts only when a prerequisite request may change them.
    return derive_publication_readiness(article, **facts)
