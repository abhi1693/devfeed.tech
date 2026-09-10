"""Explicit per-source publication authority, with previews and versioned decisions."""

from sqlalchemy import select

from devfeed_core.analysis import analysis_candidates, catalog, snapshot_hash, source_snapshot
from devfeed_core.editorial import (
    EditorialDecision,
    decide_article,
    meaningful_text,
    publication_blockers,
)
from devfeed_core.models import ArticleContent, ArticlePublicationDecision, ArticleReview

POLICY_VERSION = "trusted-source-v1"
ACTOR = "devfeed:automatic-publication"


def evaluate_publication(session, article, job, *, taxonomy=None) -> dict:
    sources = sorted(
        (
            origin.source
            for origin in article.origins
            if origin.source.approval_status == "approved"
            and origin.source.enabled
            and origin.source.publication_policy in {"preview", "auto"}
        ),
        key=lambda source: (source.publication_policy != "auto", str(source.id)),
    )
    source = sources[0] if sources else None
    reasons = [value for value in publication_blockers(article) if value != "not_approved"]
    if source is None:
        reasons.append("source_policy_manual")
    if article.review_status != "pending" or article.publication_status != "unpublished":
        reasons.append("editorial_decision_exists")
    if not meaningful_text(article.summary):
        reasons.append("missing_source_summary")
    if session.scalar(
        select(ArticleReview.id)
        .where(ArticleReview.article_id == article.id, ArticleReview.automation == {})
        .limit(1)
    ):
        reasons.append("human_review_required")
    current = source_snapshot(article, session.get(ArticleContent, article.id))
    if (
        job is None
        or job.status != "succeeded"
        or job.outcome != "applied"
        or job.input_hash != snapshot_hash(current)
        or article.classification_provenance.get("analysis_id") != str(job.id)
    ):
        reasons.append("current_analysis_required")
    elif job.result.get("reasons"):
        reasons.append("analysis_uncertain")
    if job is not None and job.catalog_hash != snapshot_hash(
        analysis_candidates(taxonomy if taxonomy is not None else catalog(session), current)
    ):
        reasons.append("current_catalog_required")
    return {
        "policy_version": POLICY_VERSION,
        "mode": source.publication_policy if source else "manual",
        "source_id": str(source.id) if source else None,
        "source_policy_revision": source.publication_policy_revision if source else None,
        "analysis_id": str(job.id) if job else None,
        "article_revision": article.editorial_revision,
        "input_hash": snapshot_hash(current),
        "status": "blocked" if reasons else "would_publish",
        "reasons": sorted(set(reasons)),
    }


def apply_publication_policy(session, article, job, *, taxonomy=None) -> dict:
    """Caller owns source, article and taxonomy locks, in that order."""
    decision = evaluate_publication(session, article, job, taxonomy=taxonomy)
    fingerprint = snapshot_hash({"article_id": str(article.id), **decision})
    previous = session.scalar(
        select(ArticlePublicationDecision).where(
            ArticlePublicationDecision.fingerprint == fingerprint
        )
    )
    if previous:
        return previous.decision
    if decision["status"] == "would_publish" and decision["mode"] == "auto":
        for action in ("approve", "publish"):
            decide_article(
                session,
                article.id,
                EditorialDecision(
                    action=action,
                    actor=ACTOR,
                    expected_revision=article.editorial_revision,
                    note=f"Automatic publication policy {POLICY_VERSION}; analysis {job.id}",
                ),
                automation=decision,
            )
        decision = {**decision, "status": "published"}
    job.result = {**job.result, "publication_policy": decision}
    session.add(
        ArticlePublicationDecision(
            article_id=article.id, fingerprint=fingerprint, decision=decision
        )
    )
    return decision
