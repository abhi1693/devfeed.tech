"""Retained processing facts for tests of publication's downstream behavior."""

from devfeed_core import analysis
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.models import (
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleImageJob,
    utcnow,
)
from sqlalchemy import select


def complete_processing(session, article):
    """Complete fixture work without changing classifications, reviews or publication."""
    article.ai_summary = article.ai_summary or "A guide to building reliable software applications."
    article.ai_description = article.ai_description or "Practical software engineering guidance."
    session.flush()
    for model in (ArticleEnrichmentJob, ArticleAnalysisJob, ArticleImageJob):
        for job in session.scalars(
            select(model).where(
                model.article_id == article.id, model.status.in_(["queued", "running"])
            )
        ):
            finish_job(job, "superseded", utcnow())
    extraction = ArticleEnrichmentJob(
        article_id=article.id,
        result={"input_url": article.canonical_url, "image_url": article.image_url},
    )
    session.add(extraction)
    finish_job(extraction, "enriched", utcnow())
    snapshot = analysis.source_snapshot(article, session.get(ArticleContent, article.id))
    candidates = analysis.analysis_candidates(analysis.catalog(session), snapshot)
    completed = ArticleAnalysisJob(
        article_id=article.id,
        input_hash=analysis.snapshot_hash(snapshot),
        input_snapshot=snapshot,
        catalog_hash=analysis.snapshot_hash(candidates),
        catalog_snapshot=candidates,
        editorial_revision=article.editorial_revision,
        prompt_version=analysis.PROMPT_VERSION,
        result={"outcome": "ready", "developer_relevance": "relevant", "page_kind": "article"},
    )
    session.add(completed)
    finish_job(completed, "applied", utcnow())
    session.flush()
    article.classification_provenance = {
        **(article.classification_provenance or {}),
        "ai_generation": {
            "analysis_id": str(completed.id),
            "input_hash": completed.input_hash,
            "prompt_version": completed.prompt_version,
        },
    }
    session.flush()
    return completed
