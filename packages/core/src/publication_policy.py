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
from devfeed_core.models import (
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticlePublicationDecision,
    ArticleReview,
)

POLICY_VERSION = "trusted-source-v3"
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
    current = source_snapshot(article, session.get(ArticleContent, article.id))
    # Feeds can supply a short teaser even when extraction provides enough source
    # evidence. Display-summary requirements are enforced by publication_blockers.
    if not meaningful_text(article.summary) and not meaningful_text(current["text"]):
        reasons.append("insufficient_source_text")
    enrichment = session.scalar(
        select(ArticleEnrichmentJob)
        .where(ArticleEnrichmentJob.article_id == article.id)
        .order_by(ArticleEnrichmentJob.created_at.desc(), ArticleEnrichmentJob.id.desc())
        .limit(1)
    )
    paywalled = (
        enrichment is not None
        and enrichment.status == "succeeded"
        and (enrichment.result or {}).get("paywalled") is True
    )
    if paywalled:
        reasons.append("paywalled_content")
    if not full and session.scalar(
        select(ArticleReview.id)
        .where(ArticleReview.article_id == article.id, ArticleReview.automation == {})
        .limit(1)
    ):
        reasons.append("human_review_required")
    if (
        job is None
        or job.status != "succeeded"
        or job.outcome not in {"applied", "insufficient_evidence"}
        or job.input_hash != snapshot_hash(current)
        or job.editorial_revision != article.editorial_revision
        or (
            job.outcome == "applied"
            and article.classification_provenance.get("analysis_id") != str(job.id)
        )
    ):
        reasons.append("current_analysis_required")
    # Reasons explain the classification; a nonempty rationale is not uncertainty.
    # Use the structured result so unresolved evidence still blocks publication.
    elif job.outcome == "insufficient_evidence":
        reasons.append("insufficient_analysis_evidence")
    elif (
        job.result.get("outcome") != "ready"
        or job.result.get("developer_relevance") != "relevant"
        or job.result.get("page_kind") != "article"
    ):
        reasons.append("analysis_uncertain")
    if job is not None and not analysis_catalog_current(
        job, taxonomy if taxonomy is not None else catalog(session), current
    ):
        reasons.append("current_catalog_required")
    return {
        "policy_version": "full-automation-v5" if full else POLICY_VERSION,
        "mode": "auto" if full and source else source.publication_policy if source else "manual",
        "full_automation": full,
        "source_id": str(source.id) if source else None,
        "source_policy_revision": source.publication_policy_revision if source else None,
        "analysis_id": str(job.id) if job else None,
        "paywall_enrichment_id": str(enrichment.id) if paywalled else None,
        "paywall_reason": (enrichment.result or {}).get("paywall_reason") if paywalled else None,
        "article_revision": article.editorial_revision,
        "input_hash": snapshot_hash(current),
        "status": "blocked" if reasons else "would_publish",
        "reasons": sorted(set(reasons)),
    }


def apply_publication_policy(session, article, job, *, taxonomy=None) -> dict:
    """Caller owns source, article and taxonomy locks, in that order."""
    decision = evaluate_publication(session, article, job, taxonomy=taxonomy)
    if (
        "paywalled_content" in decision["reasons"]
        and article.review_status == "pending"
        and article.publication_status == "unpublished"
    ):
        # An explicit publisher gate is a deterministic source-access policy
        # outcome. It does not depend on model confidence or article text length.
        decision = {**decision, "status": "would_reject", "reasons": ["paywalled_content"]}
    elif (
        get_settings().full_automation
        # Failure to prove eligibility is not evidence that an article is unsuitable.
        # Reuse the freshness checks above before trusting any negative classification.
        and job is not None
        and job.status == "succeeded"
        and job.outcome in {"applied", "insufficient_evidence"}
        and job.input_hash == decision["input_hash"]
        and job.editorial_revision == article.editorial_revision
        and job.id
        == session.scalar(
            select(ArticleAnalysisJob.id)
            .where(ArticleAnalysisJob.article_id == article.id)
            .order_by(ArticleAnalysisJob.created_at.desc(), ArticleAnalysisJob.id.desc())
            .limit(1)
        )
        and not {
            "current_catalog_required",
            "source_policy_manual",
        }.intersection(decision["reasons"])
        # Publication needs a complete ready result, but rejection only needs a
        # current explicit negative on an independent classification dimension.
        # Missing language, summary, or topic evidence must not keep an article
        # that is clearly unrelated or a utility page in the pending queue.
        and (
            job.result.get("developer_relevance") == "unrelated"
            or job.result.get("page_kind") == "non_article"
        )
        and "insufficient_source_text" not in decision["reasons"]
    ):
        if article.review_status != "pending" or article.publication_status != "unpublished":
            return decision
        negative_reasons = []
        if job.result.get("developer_relevance") == "unrelated":
            negative_reasons.append("unrelated_content")
        if job.result.get("page_kind") == "non_article":
            negative_reasons.append("non_article_content")
        decision = {**decision, "status": "would_reject", "reasons": negative_reasons}
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
        note = (
            "DevFeed automation rejected this article because the publisher explicitly gates its "
            "content behind a paywall."
            if decision["reasons"] == ["paywalled_content"]
            else "Full automation found an explicit negative content classification: "
            + ", ".join(decision["reasons"])[:900]
        )
        decide_article(
            session,
            article.id,
            EditorialDecision(
                action="reject",
                actor=ACTOR,
                expected_revision=article.editorial_revision,
                note=note,
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
