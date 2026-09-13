"""Explicit per-source publication authority, with previews and versioned decisions."""

from sqlalchemy import select

from devfeed_core.analysis import analysis_catalog_current, catalog, snapshot_hash, source_snapshot
from devfeed_core.config import get_settings
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
    full = get_settings().full_automation
    sources = sorted(
        (
            origin.source
            for origin in article.origins
            if origin.source.approval_status == "approved"
            and origin.source.enabled
            and (full or origin.source.publication_policy in {"preview", "auto"})
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
    if not full and session.scalar(
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
        or job.editorial_revision != article.editorial_revision
        or article.classification_provenance.get("analysis_id") != str(job.id)
    ):
        reasons.append("current_analysis_required")
    elif job.result.get("reasons"):
        reasons.append("analysis_uncertain")
    if job is not None and not analysis_catalog_current(
        job, taxonomy if taxonomy is not None else catalog(session), current
    ):
        reasons.append("current_catalog_required")
    return {
        "policy_version": "full-automation-v1" if full else POLICY_VERSION,
        "mode": "auto" if full and source else source.publication_policy if source else "manual",
        "full_automation": full,
        "source_id": str(source.id) if source else None,
        "source_policy_revision": source.publication_policy_revision if source else None,
        "analysis_id": str(job.id) if job else None,
        "article_revision": article.editorial_revision,
        "input_hash": snapshot_hash(current),
        "status": "blocked" if reasons else "would_publish",
        "reasons": sorted(set(reasons)),
    }


def apply_publication_policy(
    session, article, job, *, taxonomy=None, rejection_reasons=None
) -> dict:
    """Caller owns source, article and taxonomy locks, in that order."""
    decision = evaluate_publication(session, article, job, taxonomy=taxonomy)
    if rejection_reasons is not None and get_settings().full_automation:
        if article.review_status != "pending" or article.publication_status != "unpublished":
            return decision
        decision = {**decision, "status": "would_reject", "reasons": sorted(set(rejection_reasons))}
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
                    note=f"Policy {decision['policy_version']}; analysis {job.id}",
                ),
                automation=decision,
            )
        decision = {**decision, "status": "published"}
    elif decision["status"] == "would_reject":
        decide_article(
            session,
            article.id,
            EditorialDecision(
                action="reject",
                actor=ACTOR,
                expected_revision=article.editorial_revision,
                note="Full automation could not establish publication eligibility: "
                + ", ".join(decision["reasons"])[:900],
            ),
            automation=decision,
        )
        decision = {**decision, "status": "rejected"}
    if job is not None:
        job.result = {**job.result, "publication_policy": decision}
    session.add(
        ArticlePublicationDecision(
            article_id=article.id, fingerprint=fingerprint, decision=decision
        )
    )
    return decision
