"""Resume pending articles and give every completed attempt an editorial outcome."""

import uuid
from datetime import timedelta

from sqlalchemy import func, select

from devfeed_core.ai_content import eligible_article, eligible_articles
from devfeed_core.analysis import (
    PROMPT_VERSION,
    analysis_catalog_current,
    candidate_evidence,
    candidate_score,
    catalog,
    request_analysis,
    snapshot_hash,
    source_snapshot,
)
from devfeed_core.article_jobs import approved_sources, request_article_enrichment
from devfeed_core.catalog_cache import snapshot as catalog_snapshot
from devfeed_core.config import get_settings
from devfeed_core.editorial import EditorialDecision, decide_article, meaningful_text
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
from devfeed_core.tag_topic_discovery import record_keys
from devfeed_core.topics import TopicWrite, lock_topics

CHECK_INTERVAL = timedelta(minutes=5)
TOPIC_WAIT = timedelta(hours=24)
SOURCE_ACTOR = "devfeed:source-automation"


def lock_article_catalog(session):
    # Serialize only proposal creation; ordinary classification applications use
    # a shared catalog lock. Acquire this before any catalog lock to avoid upgrades.
    # DFR namespace, source proposals; lane 0 is topic decision admission.
    session.execute(select(func.pg_advisory_xact_lock(0x444652, 1)))
    lock_topics(session, read=True)


def pending_topic_matches(session, snapshot) -> bool:
    # Matching consumes identities only. Avoid transferring full descriptions,
    # facts and evidence for every pending draft while holding publication locks.
    proposed = TopicProposal.proposed
    statement = select(
        func.jsonb_build_object(
            "name",
            proposed["name"],
            "slug",
            proposed["slug"],
            "aliases",
            func.coalesce(proposed["aliases"], func.jsonb_build_array()),
            "keywords",
            func.coalesce(proposed["keywords"], func.jsonb_build_array()),
        )
    ).where(TopicProposal.status == "pending")
    candidates = catalog_snapshot(
        session, ("topic_proposals",), lambda: list(session.scalars(statement))
    )
    evidence = candidate_evidence(snapshot)
    return any(candidate_score(draft, snapshot, evidence=evidence) > 0 for draft in candidates)


def propose_source_topics(session, article) -> int:
    """Serialize only actual proposal candidates; never approve identities."""
    if (
        not get_settings().full_automation
        or not get_settings().article_topic_proposals_enabled
        or not eligible_article(article)
    ):
        return 0
    statement = (
        select(Tag)
        .join(ArticleTag)
        .where(
            ArticleTag.article_id == article.id,
            ArticleTag.origin == "source",
            Tag.auto_link_topic.is_(True),
            Tag.topic_id.is_(None),
            ~select(TopicProposal.id).where(TopicProposal.slug == Tag.slug).exists(),
        )
        .order_by(Tag.id)
        .limit(get_settings().automation_batch_size)
    )
    if session.scalar(select(statement.exists())) is not True:
        return 0
    lock_article_catalog(session)
    tags = session.scalars(statement).all()
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
    from devfeed_core.source_relevance import schedule_pending_reviews

    return schedule_pending_reviews(factory, get_settings().automation_batch_size)


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
                eligible_articles(),
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
                or not eligible_article(article)
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
            if enrichment is not None and enrichment.status == "failed":
                content = session.get(ArticleContent, identifier)
                if not meaningful_text(article.summary) and not meaningful_text(
                    content.text if content is not None else None
                ):
                    decide_article(
                        session,
                        identifier,
                        EditorialDecision(
                            action="reject",
                            actor="DevFeed automation",
                            note=(
                                "Article enrichment failed after retry exhaustion; no publisher "
                                f"summary or article text is available. {enrichment.error or ''}"
                            )[:1000],
                            expected_revision=article.editorial_revision,
                        ),
                        automation={
                            "policy": "article-enrichment-failure-v1",
                            "job_id": str(enrichment.id),
                            "error": enrichment.error,
                        },
                    )
                    counts["articles_rejected"] += 1
                    continue
            if job is None and enrichment is None:
                request_article_enrichment(session, identifier, automatic=True)
                continue
            counts["source_topics_proposed"] += propose_source_topics(session, article)
            lock_topics(session, read=True)
            taxonomy = catalog(session)
            snapshot = source_snapshot(article, session.get(ArticleContent, identifier))
            current = job is not None and (
                job.input_hash == snapshot_hash(snapshot)
                and job.editorial_revision == article.editorial_revision
                and analysis_catalog_current(job, taxonomy, snapshot)
                and job.prompt_version == PROMPT_VERSION
                and job.outcome not in {"superseded", "content_date_deferred"}
            )
            if not meaningful_text(snapshot["text"]):
                reasons = ["insufficient_source_text"]
            elif not current:
                # A current editorial revision is required even when the input hash is unchanged.
                request_analysis(session, identifier, automatic=True, force=True, taxonomy=taxonomy)
                continue
            elif job.status == "failed":
                reasons = ["analysis_failed", job.error or "analysis_attempts_exhausted"]
            elif job.result.get("topic_match_status") == "no_topic_match":
                reasons = ["no_supported_topic_match"]
            elif job.result.get("topic_match_status") == "no_primary_topic":
                reasons = ["no_supported_primary_topic"]
            else:
                decision = evaluate_publication(session, article, job, taxonomy=taxonomy)
                if decision["status"] == "would_publish":
                    apply_publication_policy(session, article, job, taxonomy=taxonomy)
                    counts["articles_published"] += 1
                    continue
                reasons = decision["reasons"]
                if job.outcome == "insufficient_evidence":
                    reasons = ["insufficient_analysis_evidence"]
                # Allow matching topic research to finish before recording a blocked decision.
                if (
                    job.result.get("developer_relevance") != "unrelated"
                    and (
                        "missing_active_primary_topic" in reasons
                        or job.outcome == "insufficient_evidence"
                    )
                    and now < article.automation_started_at + TOPIC_WAIT
                    and pending_topic_matches(session, snapshot)
                ):
                    continue
            decision = apply_publication_policy(
                session,
                article,
                job,
                taxonomy=taxonomy,
                rejection_reasons=reasons,
            )
            if decision["status"] == "rejected":
                counts["articles_rejected"] += 1
    return counts
