"""Resume pending articles and give every completed attempt an editorial outcome."""

import uuid
from datetime import timedelta

from sqlalchemy import select

from devfeed_core.analysis import (
    PROMPT_VERSION,
    analysis_candidates,
    candidate_score,
    catalog,
    request_analysis,
    snapshot_hash,
    source_snapshot,
)
from devfeed_core.article_jobs import approved_sources, request_article_enrichment
from devfeed_core.config import get_settings
from devfeed_core.editorial import meaningful_text
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleOrigin,
    ArticleTag,
    Source,
    Tag,
    Topic,
    TopicProposal,
    utcnow,
)
from devfeed_core.publication_policy import apply_publication_policy, evaluate_publication
from devfeed_core.schemas import SourceDecision
from devfeed_core.services import review_source
from devfeed_core.tag_topic_discovery import record_keys
from devfeed_core.topics import TopicWrite, lock_topics

CHECK_INTERVAL = timedelta(minutes=5)
TOPIC_WAIT = timedelta(hours=24)
SOURCE_ACTOR = "devfeed:source-automation"


def propose_source_topics(session, article) -> int:
    """Caller holds the catalog lock. Source labels propose identities, never approve them."""
    if not get_settings().full_automation:
        return 0
    tags = session.scalars(
        select(Tag)
        .join(ArticleTag)
        .where(
            ArticleTag.article_id == article.id,
            ArticleTag.origin == "source",
            Tag.auto_link_topic.is_(True),
            Tag.topic_id.is_(None),
        )
        .order_by(Tag.id)
        .limit(get_settings().automation_batch_size)
    ).all()
    if not tags:
        return 0
    slugs = [tag.slug for tag in tags]
    attempted = set(
        session.scalars(select(TopicProposal.slug).where(TopicProposal.slug.in_(slugs)))
    )
    keys = {tag.id: set(record_keys(tag)) for tag in tags}
    # Inactive catalog identities and previous rejections must not be recreated.
    existing = set()
    for terms in session.scalars(
        select(Topic.identity_keys).where(
            Topic.identity_keys.overlap(sorted(set().union(*keys.values())))
        )
    ):
        existing.update(terms)
    count = 0
    for tag in tags:
        if tag.slug in attempted or keys[tag.id] & existing:
            continue
        draft = TopicWrite(name=tag.name, slug=tag.slug, kind="unclassified")
        session.add(
            TopicProposal(
                batch_id=uuid.uuid4(),
                slug=tag.slug,
                action="create",
                origin="article_enrichment",
                source_name="Source tag discovery",
                proposed=draft.model_dump(mode="json"),
                evidence=[
                    {
                        "kind": "source_tag",
                        "tag_id": str(tag.id),
                        "article_id": str(article.id),
                        "url": article.canonical_url,
                    }
                ],
                created_by={
                    "subject": SOURCE_ACTOR,
                    "issuer": "devfeed",
                    "name": "Full automation",
                },
                research_requested=True,
            )
        )
        attempted.add(tag.slug)
        count += 1
    session.flush()
    return count


def schedule_source_admission(factory) -> int:
    if not get_settings().full_automation:
        return 0
    with factory.begin() as session:
        sources = session.scalars(
            select(Source)
            .where(Source.approval_status == "pending", Source.enabled.is_(True))
            .order_by(Source.created_at, Source.id)
            .limit(get_settings().automation_batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        for source in sources:
            review_source(
                session,
                source.id,
                SourceDecision(
                    decision="approved",
                    actor=SOURCE_ACTOR,
                    note="Full automation: validated feed admitted; "
                    "articles require independent analysis.",
                ),
            )
        return len(sources)


def schedule_article_automation(factory) -> dict[str, int]:
    counts = {
        "articles_checked": 0,
        "articles_published": 0,
        "articles_rejected": 0,
        "source_topics_proposed": 0,
    }
    if not get_settings().full_automation:
        return counts
    now = utcnow()
    origin = (
        select(ArticleOrigin.id)
        .join(Source)
        .where(
            ArticleOrigin.article_id == Article.id,
            Source.approval_status == "approved",
            Source.enabled.is_(True),
        )
    )
    with factory() as session:
        identifiers = session.scalars(
            select(Article.id)
            .where(
                Article.review_status == "pending",
                Article.publication_status == "unpublished",
                Article.automation_next_check_at <= now,
                origin.exists(),
            )
            .order_by(Article.automation_next_check_at, Article.id)
            .limit(get_settings().automation_batch_size)
        ).all()
    for identifier in identifiers:
        with factory.begin() as session:
            # Workers acquire job -> source -> article -> taxonomy. Use the same order.
            job = session.scalar(
                select(ArticleAnalysisJob)
                .where(ArticleAnalysisJob.article_id == identifier)
                .order_by(ArticleAnalysisJob.created_at.desc(), ArticleAnalysisJob.id.desc())
                .limit(1)
                .with_for_update()
            )
            approved_sources(session, identifier, lock=True)
            article = session.scalar(
                select(Article).where(Article.id == identifier).with_for_update(of=Article)
            )
            if (
                article is None
                or article.review_status != "pending"
                or article.publication_status != "unpublished"
                or article.automation_next_check_at > now
                or not any(
                    o.source.enabled and o.source.approval_status == "approved"
                    for o in article.origins
                )
            ):
                continue
            article.automation_started_at = article.automation_started_at or now
            article.automation_next_check_at = now + CHECK_INTERVAL
            counts["articles_checked"] += 1
            if job is not None and job.status in {"queued", "running"}:
                continue  # Includes provider cooldowns; durable retries own this work.
            enrichment = session.scalar(
                select(ArticleEnrichmentJob)
                .where(ArticleEnrichmentJob.article_id == identifier)
                .order_by(ArticleEnrichmentJob.created_at.desc(), ArticleEnrichmentJob.id.desc())
                .limit(1)
            )
            if enrichment is not None and enrichment.status in {"queued", "running"}:
                continue
            if job is None and enrichment is None:
                request_article_enrichment(session, identifier, automatic=True)
                continue
            lock_topics(session)
            counts["source_topics_proposed"] += propose_source_topics(session, article)
            taxonomy = catalog(session)
            snapshot = source_snapshot(article, session.get(ArticleContent, identifier))
            current = job is not None and (
                job.input_hash == snapshot_hash(snapshot)
                and job.editorial_revision == article.editorial_revision
                and job.catalog_hash == snapshot_hash(analysis_candidates(taxonomy, snapshot))
                and job.prompt_version == PROMPT_VERSION
                and job.outcome != "superseded"
            )
            if not meaningful_text(snapshot["text"]):
                reasons = ["insufficient_source_text"]
            elif not current:
                # A current editorial revision is required even when the input hash is unchanged.
                request_analysis(session, identifier, automatic=True, force=True)
                continue
            elif job.status == "failed":
                reasons = ["analysis_failed", job.error or "analysis_attempts_exhausted"]
            else:
                decision = evaluate_publication(session, article, job, taxonomy=taxonomy)
                if decision["status"] == "would_publish":
                    apply_publication_policy(session, article, job, taxonomy=taxonomy)
                    counts["articles_published"] += 1
                    continue
                reasons = decision["reasons"]
                if job.outcome == "insufficient_evidence":
                    reasons = ["insufficient_analysis_evidence"]
                # Allow matching topic research to finish before rejecting for a missing topic.
                if (
                    job.result.get("developer_relevance") != "unrelated"
                    and (
                        "missing_active_primary_topic" in reasons
                        or job.outcome == "insufficient_evidence"
                    )
                    and now < article.automation_started_at + TOPIC_WAIT
                ):
                    proposals = session.scalars(
                        select(TopicProposal.proposed).where(TopicProposal.status == "pending")
                    )
                    if any(candidate_score(proposed, snapshot) > 0 for proposed in proposals):
                        continue
            apply_publication_policy(
                session,
                article,
                job,
                taxonomy=taxonomy,
                rejection_reasons=reasons,
            )
            counts["articles_rejected"] += 1
    return counts
